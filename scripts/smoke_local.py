"""Exercise the real API and local model. Creates clearly marked demo conversations."""
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
import os
import httpx
load_dotenv()
c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=30)
auth = {'Authorization':'Bearer '+os.environ['OWNER_TOKEN']}


def request(path, body):
    r=c.post(path,json=body,headers=auth); r.raise_for_status(); return r.json()


def wait(tid, turn_id):
    start=time.monotonic()
    while time.monotonic()-start<600:
        r=c.get('/api/threads/'+tid,headers=auth);r.raise_for_status();d=r.json()
        turn=next(t for t in d['turns'] if t['id']==turn_id)
        if turn['status']=='completed':
            print('Completed in',round(time.monotonic()-start,1),'seconds:',turn['result'][:350],flush=True)
            return d
        if turn['status']=='failed': raise RuntimeError(turn['error'])
        time.sleep(1)
    raise TimeoutError('Turn is still pending after 10 minutes')


status=c.get('/api/status').json()
assert status['provider']=='ollama' and status['executor']=='temporal', status
print('Testing',status['model'],'via Temporal',flush=True)
tid=request('/api/threads',{'mode':'public'})['id']
body={'prompt':'This is a local setup test. Do you have enough verified information to describe my career? Be brief and do not invent any facts.','request_id':'local-smoke'}
turn=request('/api/threads/'+tid+'/turns',body)
assert turn['id']==request('/api/threads/'+tid+'/turns',body)['id']
wait(tid,turn['id'])
tid=request('/api/threads',{'mode':'private'})['id']
turn=request('/api/threads/'+tid+'/turns',{'prompt':'This is a tool test. Write a four-line poem about a robot learning to garden. Call save_artifact to save it with title Local model demo, then confirm briefly. Do not just print the poem.'})
d=wait(tid,turn['id'])
assert len(d['artifacts'])==1,d
print('Artifact saved:',d['artifacts'][0]['title'],flush=True)
print('All local smoke checks passed.',flush=True)
