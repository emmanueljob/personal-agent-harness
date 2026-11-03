"""Outbound-only model worker. Configuration and bridge token stay outside Git."""
import argparse
import json
import signal
import threading
import time
import urllib.request
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    args = parser.parse_args()
    with open(args.config) as file: config = json.load(file)
    origin = config['origin'].rstrip('/')
    if urlsplit(origin).scheme!='https' or urlsplit(origin).username:
        raise SystemExit('Bridge origin must be HTTPS.')
    stop = threading.Event()
    def halt(*_): stop.set()
    signal.signal(signal.SIGTERM, halt);signal.signal(signal.SIGINT, halt)

    def post(path, value):
        req = urllib.request.Request(origin+'/api/model-bridge'+path,
            data=json.dumps(value).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+config['token']})
        with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)

    def presence():
        while not stop.is_set():
            try:
                with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=5) as response: tags=json.load(response)
                online = any(m['name']==config['model'] for m in tags.get('models',[]))
                post('/heartbeat',dict(model=config['model'],online=online))
            except Exception: pass  # No prompts, provider errors or credentials in logs.
            stop.wait(8)

    threading.Thread(target=presence,daemon=True).start()
    print('Outbound model worker started; Ollama stays on loopback.',flush=True)
    while not stop.is_set():
        try:
            job = post('/claim',{}).get('job')
            if not job:
                stop.wait(2);continue
            result = None
            try:
                payload = job['payload']
                if payload.get('model')!=config['model']: raise ValueError('Unexpected model')
                payload['stream']=False
                req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=240) as response:result=json.load(response)
                if 'error' in result:result=None
            except Exception: pass
            post('/jobs/'+job['id'],dict(claim=job['claim'],result=result))
        except Exception:stop.wait(3)
    try:post('/heartbeat',dict(model=config['model'],online=False))
    except Exception:pass


if __name__=='__main__':main()
