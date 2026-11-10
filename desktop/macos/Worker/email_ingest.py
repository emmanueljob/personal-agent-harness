"""Read-only Gmail connector backfill sink. Personal payloads enter over stdin only.

The owner-authorized connector orchestrator supplies MIME trees, not credentials.
Durable discovery pages and per-message receipts live beside canonical evidence.
This executable never contacts Gmail, fetches URLs or attachments, or starts inference.
"""
import argparse
import base64
import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from email.utils import getaddresses

MAX_LINE = 100 * 1024 * 1024
MAX_BODY = 4 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def setup(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS email_runs (
          id TEXT PRIMARY KEY, state TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
          query TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, pause_requested INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS email_accounts (
          run_id TEXT NOT NULL, account TEXT NOT NULL, role TEXT NOT NULL, page_token TEXT,
          discovery_complete INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run_id,account));
        CREATE TABLE IF NOT EXISTS email_queue (
          run_id TEXT NOT NULL, account TEXT NOT NULL, message_id TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(run_id,account,message_id));
    """)
    db.commit()


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]; self.hidden=0; self.quoted=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','head'):self.hidden+=1
        if tag=='blockquote':self.quoted+=1
        if not self.hidden and tag in ('p','div','br','li','tr','blockquote'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head'):self.hidden=max(0,self.hidden-1)
        if tag=='blockquote':self.quoted=max(0,self.quoted-1)
        if not self.hidden and tag in ('p','div','li','tr','blockquote'):self.parts.append('\n')
    def handle_data(self,text):
        if not self.hidden:self.parts.append(('> ' if self.quoted else '')+text)


def headers(part):
    return {h['name'].lower():h['value'] for h in part.get('headers',[]) if isinstance(h,dict) and isinstance(h.get('name'),str) and isinstance(h.get('value'),str)}


def mime_text(part, depth=0):
    if depth>30:raise ValueError('mime_depth')
    kind=part.get('mime_type','').lower()
    if part.get('filename') or headers(part).get('content-disposition','').lower().startswith('attachment') or kind=='message/rfc822':
        return '',False
    children=part.get('parts') or []
    if children:
        options=[mime_text(p,depth+1) for p in children]
        if kind=='multipart/alternative':
            for text,plain in options:
                if text and plain:return text,True
            return next(((t,p) for t,p in options if t),('',False))
        return '\n'.join(t for t,_ in options if t),all(p for t,p in options if t)
    if kind not in ('text/plain','text/html'):return '',False
    body=part.get('body') or {}
    text=body.get('content')
    if text is None and body.get('base64_url_content'):
        value=body['base64_url_content']
        raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
        charset=re.search(r'charset=["\']?([^;"\' ]+)',headers(part).get('content-type',''),re.I)
        try:text=raw.decode(charset.group(1) if charset else 'utf-8',errors='replace')
        except LookupError:text=raw.decode('utf-8',errors='replace')
    if not isinstance(text,str):return '',False # attachment_id alone is not downloadable body text
    if len(text)>MAX_BODY:raise ValueError('body_too_large')
    if kind=='text/html':
        parser=TextHTML();parser.feed(text);text=''.join(parser.parts)
    text=text.replace('\x00','')
    if len(text)>MAX_BODY:raise ValueError('body_too_large')
    return text,kind=='text/plain'


def authored_text(text):
    """Conservative quote/signature removal; original extracted body stays as evidence."""
    result=[]
    lines=text.splitlines()
    for i,line in enumerate(lines):
        clean=line.strip()
        if clean=='--' or re.match(r'(?i)^-+\s*(original message|forwarded message)',clean):break
        if re.match(r'(?i)^on .{0,500}wrote:\s*$',clean):break
        if re.match(r'(?i)^from:\s+',clean) and any(re.match(r'(?i)^(sent|date|to|subject):',v.strip()) for v in lines[i+1:i+5]):break
        if clean.startswith('>'):continue
        result.append(line)
    return '\n'.join(result).strip()


def record(account, role, message, start, end):
    ident=message.get('id')
    if not isinstance(ident,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,200}',ident):raise ValueError('invalid_id')
    labels=message.get('label_ids') or []
    if any(v in labels for v in ('SPAM','TRASH','DRAFT')):return None
    occurred=int(message['internal_date'])/1000
    if not start<=occurred<end:return None
    part=message.get('payload') or {}; meta=headers(part)
    body,_=mime_text(part)
    if len(body)>MAX_BODY:raise ValueError('body_too_large')
    senders=[address.lower() for _,address in getaddresses([meta.get('from','')])]
    owner=role=='personal' and 'SENT' in labels and senders==[account.lower()]
    basis='owner' if owner else 'shared_mail' if role=='shared' else 'email_received'
    metadata=dict(parser_version='email-mime-v1.1',account=account,account_role=role,speaker=basis,from_address=meta.get('from',''),
                  to=meta.get('to',''),cc=meta.get('cc',''),message_id=meta.get('message-id',''),
                  gmail_id=ident,thread_id=message.get('thread_id') or '',labels=json.dumps(sorted(labels)),
                  body_status='email_text' if body else 'email_headers_only',
                  quoted_text_policy='original retained; owner synthesis uses quote-stripped authored_text',
                  attachment_policy='attachments and remote content not fetched')
    if owner:metadata['authored_text']=authored_text(body)
    return dict(id='gmail:'+hashlib.sha256(account.lower().encode()).hexdigest()[:24]+':'+ident,
                source='gmail:'+hashlib.sha256(account.lower().encode()).hexdigest()[:24],
                kind='email',title=meta.get('subject','(No subject)'),body=body,occurred=occurred,metadata=metadata)


def status(db, run_id):
    row=db.execute('SELECT * FROM email_runs WHERE id=?',(run_id,)).fetchone()
    if not row:raise ValueError('unknown_run')
    result=dict(row)
    result['accounts']=[]
    for a in db.execute('SELECT * FROM email_accounts WHERE run_id=? ORDER BY account',(run_id,)):
        value=dict(a)
        value['counts']={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM email_queue WHERE run_id=? AND account=? GROUP BY state',(run_id,a['account']))}
        result['accounts'].append(value)
    return result


def handle(db, value):
    action=value['action'];run_id=value['run_id']
    if action=='begin':
        accounts=value['accounts']
        if not accounts or len(accounts)>10:raise ValueError('invalid_accounts')
        with db:
            prior=db.execute('SELECT * FROM email_runs WHERE id=?',(run_id,)).fetchone()
            if prior:
                old=[dict(r) for r in db.execute('SELECT account,role FROM email_accounts WHERE run_id=? ORDER BY account',(run_id,))]
                if prior['start']!=value['start'] or prior['end']!=value['end'] or prior['query']!=value['query'] or old!=sorted(accounts,key=lambda a:a['account']):
                    raise ValueError('run_selection_changed')
            else:
                db.execute("INSERT INTO email_runs(id,state,start,end,query,created,updated) VALUES (?,'discovering',?,?,?,?,?)",(run_id,value['start'],value['end'],value['query'],time.time(),time.time()))
                for account in accounts:
                    if account['role'] not in ('personal','shared'):raise ValueError('invalid_role')
                    db.execute('INSERT INTO email_accounts(run_id,account,role) VALUES (?,?,?)',(run_id,account['account'].lower(),account['role']))
    elif action=='page':
        account=value['account']; ids=value['message_ids']
        if len(ids)>500 or not all(isinstance(v,str) and re.fullmatch('[a-zA-Z0-9_-]{1,200}',v) for v in ids):raise ValueError('invalid_ids')
        if not db.execute('SELECT 1 FROM email_accounts WHERE run_id=? AND account=?',(run_id,account)).fetchone():raise ValueError('unknown_account')
        with db:
            for ident in ids:db.execute('INSERT OR IGNORE INTO email_queue(run_id,account,message_id) VALUES (?,?,?)',(run_id,account,ident))
            token=value.get('next_page_token') or None
            db.execute('UPDATE email_accounts SET page_token=?,discovery_complete=? WHERE run_id=? AND account=?',(token,int(token is None),run_id,account))
            db.execute('UPDATE email_runs SET updated=? WHERE id=?',(time.time(),run_id))
    elif action=='pending':
        if value.get('retry_failed'):
            with db:db.execute("UPDATE email_queue SET state='pending',error='' WHERE run_id=? AND state='failed'",(run_id,))
        rows=db.execute("SELECT account,message_id FROM email_queue WHERE run_id=? AND state='pending' ORDER BY account,message_id LIMIT ?",(run_id,max(1,min(500,int(value.get('limit',100)))))).fetchall()
        return dict(pending=[dict(r) for r in rows])
    elif action=='reprocess':
        ids=value['message_ids']
        if len(ids)>100:raise ValueError('batch_limit')
        with db:
            for ident in ids:
                db.execute("UPDATE email_queue SET state='pending',error='' WHERE run_id=? AND account=? AND message_id=?",(run_id,value['account'],ident))
            db.execute("UPDATE email_runs SET state='importing',updated=? WHERE id=?",(time.time(),run_id))
    elif action=='messages':
        run=db.execute('SELECT * FROM email_runs WHERE id=?',(run_id,)).fetchone()
        if not run or run['pause_requested']:return dict(paused=True)
        account=value['account']
        selection=db.execute('SELECT role FROM email_accounts WHERE run_id=? AND account=?',(run_id,account)).fetchone()
        if not selection:raise ValueError('unknown_account')
        messages=value['messages']
        if len(messages)>100:raise ValueError('batch_limit')
        with db:
            for message in messages:
                ident=message.get('id')
                queued=db.execute("SELECT state FROM email_queue WHERE run_id=? AND account=? AND message_id=?",(run_id,account,ident)).fetchone()
                if not queued:raise ValueError('unqueued_message')
                if queued['state']=='imported':continue
                try:r=record(account,selection['role'],message,run['start'],run['end'])
                except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
                    db.execute("UPDATE email_queue SET state='failed',error='unsupported_message' WHERE run_id=? AND account=? AND message_id=?",(run_id,account,ident));continue
                if r:
                    payload=json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                    db.execute("""INSERT INTO evidence(id,source,kind,title,body,occurred,payload,hash) VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET source=excluded.source,kind=excluded.kind,title=excluded.title,body=excluded.body,
                        occurred=excluded.occurred,payload=excluded.payload,hash=excluded.hash,revision=evidence.revision+1
                        WHERE evidence.hash<>excluded.hash""",(r['id'],r['source'],r['kind'],r['title'],r['body'],r['occurred'],payload,digest(r)))
                db.execute("UPDATE email_queue SET state=?,error='' WHERE run_id=? AND account=? AND message_id=?",('imported' if r else 'excluded',run_id,account,ident))
            db.execute("UPDATE email_runs SET state='importing',updated=? WHERE id=?",(time.time(),run_id))
    elif action=='finish':
        pending=db.execute("SELECT count(*) FROM email_queue WHERE run_id=? AND state IN ('pending','failed')",(run_id,)).fetchone()[0]
        missing=db.execute('SELECT count(*) FROM email_accounts WHERE run_id=? AND discovery_complete=0',(run_id,)).fetchone()[0]
        with db:db.execute('UPDATE email_runs SET state=?,updated=? WHERE id=?',('needs_retry' if pending or missing else 'complete',time.time(),run_id))
    elif action=='resume':
        with db:db.execute("UPDATE email_runs SET pause_requested=0,state='importing',updated=? WHERE id=?",(time.time(),run_id))
    elif action!='status':raise ValueError('unknown_action')
    return status(db,run_id)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    args=parser.parse_args()
    # Refuse a new/different store: the app owns initialization and schema.
    path=args.home/'brain.sqlite'
    db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=15);db.row_factory=sqlite3.Row
    setup(db)
    while True:
        line=sys.stdin.buffer.readline(MAX_LINE+1)
        if not line:break
        try:
            if len(line)>MAX_LINE:raise ValueError('request_too_large')
            result=handle(db,json.loads(line))
            print(json.dumps(dict(ok=True,result=result),ensure_ascii=False),flush=True)
        except Exception:
            print(json.dumps(dict(ok=False,error='Email batch failed; committed messages and discovery checkpoints are safe.')),flush=True)


if __name__=='__main__':main()
