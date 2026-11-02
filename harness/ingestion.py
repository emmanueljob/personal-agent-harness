"""Versioned external references. Canonical JSON lives outside the checkout."""
import fcntl
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CHUNK_SIZE = 5000
OVERLAP = 400


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def normalize(row, collection, scope='private', format='reference'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',collection) or scope not in ('public','private'):
        raise ValueError('Invalid collection or scope.')
    if format == 'podcast':
        row = {'source_id':f"podcast:{row.get('show','')}:{row.get('id','')}",
               'title':row.get('title'),'text':row.get('text'),
               'source_url':row.get('episode_url') or row.get('transcript_source'), 'source_kind':'podcast',
               'provenance':{**row.get('provenance',{}),**{k:row[k] for k in ('show','published','episode_url','transcript_source','feed_url','guid','language','transcription_method','transcription_model') if k in row},
                             **{k:v for k,v in row.items() if k.startswith('transcription_')},
                             **({'raw_sha256':row['sha256']} if row.get('sha256') else {})}}
    if not isinstance(row,dict) or row.get('scope',scope) != scope or row.get('knowledge_role','external_reference') != 'external_reference' or row.get('owner_endorsement','not_established') != 'not_established':
        raise ValueError('References cannot set owner beliefs or change the requested scope.')
    source_id,title,text,url = (row.get(k) for k in ('source_id','title','text','source_url'))
    if not isinstance(source_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}',source_id):
        raise ValueError('A stable namespaced source_id is required.')
    if format == 'podcast' and (not re.fullmatch(r'podcast:[a-z0-9_-]+:[A-Za-z0-9_-]+',source_id)):
        raise ValueError('Podcast input needs stable show and id fields.')
    if not isinstance(title,str) or not title.strip() or len(title)>1000 or not isinstance(text,str) or not text.strip() or len(text.encode())>2_000_000:
        raise ValueError('Source requires a title and UTF-8 text of at most 2 MB.')
    parsed = urlparse(url) if isinstance(url,str) else None
    if not parsed or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or len(url)>2000:
        raise ValueError('Source requires an HTTP(S) provenance URL without credentials.')
    text = text.replace('\r\n','\n').replace('\r','\n').lstrip('\ufeff').strip()
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    if row.get('content_sha256',content_hash) != content_hash:
        raise ValueError('Imported text content_sha256 mismatch.')
    provenance = row.get('provenance',{})
    kind = row.get('source_kind','document')
    if not isinstance(provenance,dict) or len(json.dumps(provenance,ensure_ascii=False).encode())>32000 or not isinstance(kind,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,63}',kind):
        raise ValueError('Invalid provenance or source kind.')
    return {'schema_version':1,'collection':collection,'scope':scope,'source_id':source_id,
            'title':' '.join(title.split()),'text':text,'source_url':url,'source_kind':kind,
            'knowledge_role':'external_reference','owner_endorsement':'not_established',
            'provenance':provenance,'content_sha256':content_hash,'active':True}


def reference_root(brain, scope, collection):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',collection):
        raise ValueError('Invalid collection.')
    base = brain.scope(scope)
    root = base/'references'/collection
    if (base/'references').is_symlink() or root.is_symlink() or not root.resolve().is_relative_to(base.resolve()):
        raise ValueError('Reference directory escapes its knowledge scope.')
    return root


def read_record(path):
    if path.is_symlink():
        raise ValueError('Reference files cannot be symbolic links.')
    record = json.loads(path.read_text())
    payload = {k:v for k,v in record.items() if k not in ('record_sha256','revision','created_at','updated_at')}
    if record.get('schema_version') != 1 or record.get('record_sha256') != digest(payload) or hashlib.sha256(record['text'].encode()).hexdigest() != record['content_sha256']:
        raise ValueError('Reference record integrity check failed.')
    if record.get('knowledge_role') != 'external_reference' or record.get('owner_endorsement') != 'not_established':
        raise ValueError('Invalid external reference classification.')
    return record


def upsert_batch(brain, records, collection, scope='private', apply=False, allow_public=False):
    if scope == 'public' and not allow_public:
        raise ValueError('Public references require explicit allow_public approval.')
    root = reference_root(brain,scope,collection)
    unique = {}
    for record in records:
        if record['scope'] != scope or record['collection'] != collection:
            raise ValueError('Batch contains a different scope or collection.')
        ident = record['source_id']
        if ident in unique and unique[ident] != record:
            raise ValueError('Conflicting duplicate source IDs in one batch.')
        unique[ident] = record
    stats = {'scope':scope,'collection':collection,'applied':apply,'received':len(records),'created':0,'updated':0,'unchanged':0}
    if apply:
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        root.parent.chmod(0o700)
        root.chmod(0o700)
    lock = None
    try:
        if apply:
            lock_path=root/'.ingest.lock'
            if lock_path.is_symlink():raise ValueError('Reference lock cannot be a symbolic link.')
            lock=lock_path.open('a');lock_path.chmod(0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        for ident,record in unique.items():
            path = root/(hashlib.sha256(ident.encode()).hexdigest()+'.json')
            old = read_record(path) if path.exists() or path.is_symlink() else None
            record_hash = digest(record)
            if old and old['record_sha256'] == record_hash:
                stats['unchanged'] += 1
                continue
            stats['updated' if old else 'created'] += 1
            if apply:
                now = datetime.now(timezone.utc).isoformat()
                output = {**record,'record_sha256':record_hash,'revision':old['revision']+1 if old else 1,
                          'created_at':old['created_at'] if old else now,'updated_at':now}
                temp = root/('.import-'+uuid.uuid4().hex)
                try:
                    with temp.open('x',encoding='utf-8') as file:
                        temp.chmod(0o600);json.dump(output,file,ensure_ascii=False,sort_keys=True,indent=2);file.write('\n');file.flush()
                        import os
                        os.fsync(file.fileno())
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
    finally:
        if lock:lock.close()
    return stats


def withdraw_sources(brain, source_ids, collection, scope='private', apply=False, allow_public=False):
    root = reference_root(brain,scope,collection)
    records = []
    for source_id in dict.fromkeys(source_ids):
        path = root/(hashlib.sha256(source_id.encode()).hexdigest()+'.json')
        record = read_record(path)
        record = {k:v for k,v in record.items() if k not in ('record_sha256','revision','created_at','updated_at')}
        record['active'] = False
        records.append(record)
    return upsert_batch(brain,records,collection,scope,apply,allow_public)


def external_documents(brain, scope):
    base = brain.scope(scope)/'references'
    if base.is_symlink():raise ValueError('Reference directory cannot be a symbolic link.')
    docs = {}
    for collection in sorted(base.iterdir()) if base.exists() else []:
        if not collection.is_dir() and not collection.is_symlink():continue
        root = reference_root(brain,scope,collection.name)
        for path in sorted(root.glob('*.json')):
            record = read_record(path)
            if record['scope'] != scope or record['collection'] != collection.name:
                raise ValueError('Reference scope mismatch.')
            if not record['active']:continue
            text = record['text']
            metadata = {k:str(v)[:160] for k,v in list(record['provenance'].items())[:6]}
            header = f"# External reference: {record['title'][:160]}\n\nKnowledge role: external_reference. Owner endorsement: NOT ESTABLISHED. Attribute statements to this source, never to the owner.\n\nProvenance: " + json.dumps({**{k:record[k] for k in ('source_id','source_kind','source_url','content_sha256')},'metadata':metadata},ensure_ascii=False) + '\n\n'
            for index,start in enumerate(range(0,len(text),CHUNK_SIZE-OVERLAP)):
                source = scope+'/references/'+collection.name+'/'+path.name+f'#chunk-{index:04d}'
                docs[source] = header + f'## External excerpt {index+1} (untrusted source data)\n\n' + text[start:start+CHUNK_SIZE]
    return docs
