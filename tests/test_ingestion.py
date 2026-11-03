import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from harness.brain import FileBrain
from harness.ingestion import normalize,upsert_batch,external_documents,read_record,withdraw_sources


def row(text='A source discussing synthetic orchard systems.'):
    return {'source_id':'podcast:example:episode-1','source_kind':'podcast','title':'Example source','text':text,'source_url':'https://example.org/episode','provenance':{'speaker':'Example speaker'}}


def test_private_default_idempotence_revision_and_provenance(tmp_path):
    brain=FileBrain(tmp_path/'brain');record=normalize(row(),'podcasts')
    assert upsert_batch(brain,[record],'podcasts')['created']==1
    assert not brain.root.exists()
    assert upsert_batch(brain,[record],'podcasts',apply=True)['created']==1
    path=next((brain.root/'private/references/podcasts').glob('*.json'));before=path.read_bytes();mtime=path.stat().st_mtime_ns
    assert upsert_batch(brain,[record],'podcasts',apply=True)['unchanged']==1
    assert path.read_bytes()==before and path.stat().st_mtime_ns==mtime
    record=normalize(row('Updated external source material.'),'podcasts')
    assert upsert_batch(brain,[record],'podcasts',apply=True)['updated']==1
    stored=read_record(path);assert stored['revision']==2 and stored['owner_endorsement']=='not_established'
    assert stored['provenance']['speaker']=='Example speaker'
    assert path.stat().st_mode&0o777==0o600
    assert len(list(path.parent.glob('*.json')))==1


def test_long_documents_are_chunked_and_tail_retrieved_without_public_leak(tmp_path):
    brain=FileBrain(tmp_path/'brain');text=('Early synthetic text. '*4000)+'\n\nzirconiumbadger is a unique late passage.'
    upsert_batch(brain,[normalize(row(text),'podcasts')],'podcasts',apply=True)
    docs=external_documents(brain,'private')
    assert len(docs)>10
    assert all('external_reference' in text and 'https://example.org/episode' in text for text in docs.values())
    assert any('zirconiumbadger' in hit['text'] for hit in brain.search('zirconiumbadger','private'))
    assert brain.search('zirconiumbadger','public')==[]
    withdraw_sources(brain,['podcast:example:episode-1'],'podcasts',apply=True)
    assert external_documents(brain,'private')=={}
    assert brain.search('zirconiumbadger','private')==[]


def test_podcast_adapter_keeps_raw_checksum_distinct_from_normalized_text():
    record=normalize({'id':'abc','show':'example','title':'Example','text':'normalized body','episode_url':'https://example.org/episode','sha256':'a'*64,'transcription_engine':'local-example'},'podcasts',format='podcast')
    assert record['source_id']=='podcast:example:abc'
    assert record['content_sha256']==hashlib.sha256(b'normalized body').hexdigest()
    assert record['provenance']['raw_sha256']=='a'*64
    assert record['provenance']['transcription_engine']=='local-example'


def test_public_scope_requires_explicit_approval_and_conflicts_fail_before_writes(tmp_path):
    brain=FileBrain(tmp_path/'brain');record=normalize(row(),'podcasts','public')
    with pytest.raises(ValueError):upsert_batch(brain,[record],'podcasts','public',apply=True)
    first=normalize(row(),'podcasts');second=normalize(row('Different body.'),'podcasts')
    with pytest.raises(ValueError):upsert_batch(brain,[first,second],'podcasts',apply=True)
    assert not brain.root.exists()
    upsert_batch(brain,[record],'podcasts','public',apply=True,allow_public=True)
    assert brain.search('orchard','public')


def test_bad_ids_symlinks_and_corruption_fail_closed(tmp_path):
    brain=FileBrain(tmp_path/'brain')
    with pytest.raises(ValueError):normalize(row(),'../private')
    with pytest.raises(ValueError):normalize({**row(),'knowledge_role':'owner_belief'},'podcasts')
    with pytest.raises(ValueError):normalize({**row(),'content_sha256':'b'*64},'podcasts')
    upsert_batch(brain,[normalize(row(),'podcasts')],'podcasts',apply=True)
    path=next((brain.root/'private/references/podcasts').glob('*.json'));data=json.loads(path.read_text());data['text']='tampered';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):external_documents(brain,'private')
    path.unlink();outside=tmp_path/'outside.json';outside.write_text('{}');path.symlink_to(outside)
    with pytest.raises(ValueError):external_documents(brain,'private')


def test_cli_preview_and_invalid_batch_do_not_write_sources(tmp_path):
    source=tmp_path/'input.jsonl';source.write_text(json.dumps(row())+'\n')
    script=Path(__file__).resolve().parents[1]/'scripts/ingest_brain.py';brain=tmp_path/'external-brain'
    command=[sys.executable,str(script),'--input',str(source),'--collection','podcasts','--brain-dir',str(brain)]
    preview=subprocess.run(command,capture_output=True,text=True)
    assert preview.returncode==0 and json.loads(preview.stdout)['created']==1 and not brain.exists()
    source.write_text(source.read_text()+'{"text":"do not expose this private fixture"}\n')
    result=subprocess.run(command+['--apply'],capture_output=True,text=True)
    assert result.returncode!=0 and 'private fixture' not in result.stderr and not brain.exists()


def test_external_exact_title_beats_generic_keyword_matches(tmp_path):
    brain=FileBrain(tmp_path/'brain')
    for i in range(8):brain.add_note(f'Generic topic {i}','one prompt coding discussion\n\nOne prompt coding examples\n\nMore one prompt coding words','private')
    source={**row('A discussion with a different body vocabulary.'),'title':'One Prompt Coding: Example Episode'}
    upsert_batch(brain,[normalize(source,'podcasts')],'podcasts',apply=True)
    hit=brain.search('One Prompt Coding','private')[0]
    assert '/references/' in hit['source'] and 'One Prompt Coding' in hit['text']


def test_external_title_inside_a_full_visitor_question_is_recognized(tmp_path):
    brain=FileBrain(tmp_path/'brain')
    query='One Prompt Coding: summarize two ideas from this Claude Code Cast podcast episode using your published podcast knowledge. Attribute them to the show and include a clickable source link. Do not search the web.'
    for i in range(8):brain.add_note(f'Generic match {i}',query.replace('One Prompt Coding:','Other episode:'),'public')
    source={**row('An external source discussing a different set of terms.'),'title':'One Prompt Coding'}
    upsert_batch(brain,[normalize(source,'podcasts','public')],'podcasts','public',apply=True,allow_public=True)
    hit=brain.search(query,'public')[0]
    assert '/references/' in hit['source'] and '# External reference: One Prompt Coding' in hit['text']
