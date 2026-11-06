"""Run isolated, matched-input comparisons; never dispatch to the site's queue."""
import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from harness import agent
from harness.gbrain import GBrain
from harness.models import settings
from harness.paths import application_home,brain_directory,data_directory
from harness.store import Store
from langchain_core.messages import AIMessage
from temporalio.client import Client

CASES={
 'knowledge':('public','In at most 90 words, explain how the owner likes to lead engineers. Use approved knowledge and cite the relevant notes. Do not invent examples.'),
 'bookshelf':('public','Use browse_bookshelf to show No Rules Rules on the bookshelf. Use the actual published book record, then confirm briefly.'),
 'webpage':('public','Call read_webpage for https://agilemanifesto.org/principles.html and summarize two principles in your own words. Cite the actual webpage. Keep it under 90 words.'),
 'artifact':('private','Call save_artifact to save a Markdown document titled Comparison demo containing four short steps to test a product idea in two days. Use synthetic examples. Then confirm briefly.'),
}


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n');path.chmod(0o600)


def prepare(name,run_root):
    config=settings()
    if config['provider']!='ollama':raise ValueError('Comparison requires the existing local Ollama configuration.')
    mode,prompt=CASES[name];ident=uuid.uuid4().hex;root=run_root/name;root.mkdir(parents=True,mode=0o700)
    shared=GBrain(brain_directory())
    stores={}
    case={'id':ident,'name':name,'mode':mode,'prompt':prompt,'brain_root':str(shared.root),
          'gbrain_binary':str(shared.binary),'temporal_address':os.getenv('TEMPORAL_ADDRESS','localhost:7233'),
          'task_queue':'comparison-'+ident,'context_window':int(os.getenv('OLLAMA_CONTEXT','8192')),
          'ollama_base_url':os.getenv('OLLAMA_BASE_URL','http://127.0.0.1:11434'),
          'web_provider':os.getenv('WEB_SEARCH_PROVIDER','ddgs')}
    if case['web_provider'] not in ('ddgs','disabled'):raise ValueError('Use keyless or disabled web tools for this local experiment.')
    for engine in ('baseline','deepagents'):
        store=Store(root/engine);store.brain=shared;stores[engine]=store
        tid=store.thread(mode)['id'];turn_id=store.enqueue(tid,prompt)
        case[engine+'_root']=str(store.root);case[engine+'_thread']=tid;case[engine+'_turn']=turn_id
    captured={}
    def capture(*args,**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(get_state=lambda _:SimpleNamespace(values={'messages':[AIMessage(content='prepared')]},next=()))
    store=stores['baseline'];turn=store.query('SELECT * FROM turns WHERE id=?',(case['baseline_turn'],))[0]
    with patch.object(agent,'create_agent',capture):agent.execute(store,turn)
    body=store.query('SELECT body FROM execution_context WHERE turn_id=?',(case['baseline_turn'],))[0]['body']
    snapshot=json.loads(body)
    case.update(config=snapshot['config'],snapshot=snapshot,system_prompt=captured['system_prompt'],
                tool_names=[tool.name for tool in captured['tools']],initial_context_sha256=hashlib.sha256(body.encode()).hexdigest())
    with stores['deepagents'].connect() as db:db.execute('INSERT INTO execution_context VALUES (?,?)',(case['deepagents_turn'],body))
    write_json(root/'case.json',case)
    return case,root/'case.json'


def worker_start(case_file,log):
    env={**os.environ,'PYTHONPATH':str(ROOT)}
    return subprocess.Popen([sys.executable,'-m','experiments.deepagents_runtime','--case-file',str(case_file)],cwd=ROOT,env=env,stdout=log,stderr=log)


async def wait_idle():
    path=data_directory()/'harness.sqlite'
    if not path.exists():return
    while True:
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
            busy=db.execute("SELECT count(*) FROM turns WHERE status IN ('queued','running')").fetchone()[0]
        if not busy:return
        print('Waiting for the site’s active turn before using the shared local model.',flush=True)
        await asyncio.sleep(10)


async def history_metrics(handle):
    history=await handle.fetch_history()
    types={};attempts={};completed={};sequence=[];retry_attempts={}
    for event in history.events:
        if event.HasField('activity_task_scheduled_event_attributes'):
            name=event.activity_task_scheduled_event_attributes.activity_type.name;types[event.event_id]=name
            sequence.append(name);attempts[name]=attempts.get(name,0)+1
        if event.HasField('activity_task_completed_event_attributes'):
            name=types.get(event.activity_task_completed_event_attributes.scheduled_event_id,'unknown');completed[name]=completed.get(name,0)+1
        if event.HasField('activity_task_started_event_attributes'):
            info=event.activity_task_started_event_attributes
            name=types.get(info.scheduled_event_id,'unknown')
            retry_attempts[name]=max(retry_attempts.get(name,0),info.attempt)
    finished=any(event.HasField('workflow_execution_completed_event_attributes') or event.HasField('workflow_execution_failed_event_attributes') or event.HasField('workflow_execution_timed_out_event_attributes') for event in history.events)
    return {'scheduled':attempts,'completed':completed,'max_activity_attempt':retry_attempts,'sequence':sequence,'history_events':len(history.events),'finished':finished}


def summarize(case,engine,elapsed,status,history):
    store=Store(Path(case[engine+'_root']))
    turns=store.query('SELECT status,result,error FROM turns WHERE id=?',(case[engine+'_turn'],))
    with store.connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='comparison_metrics'").fetchone()
    metrics=store.query('SELECT kind,body FROM comparison_metrics ORDER BY id') if exists else []
    ends=[json.loads(m['body']) for m in metrics if m['kind']=='model_end']
    starts=[json.loads(m['body']) for m in metrics if m['kind']=='model_start']
    tool_calls=[name for m in ends for name in m.get('tool_calls',[])]
    artifacts=store.query('SELECT title,body FROM artifacts')
    calls=store.query('SELECT tool,body FROM tool_results')
    checks={}
    if case['name']=='bookshelf':checks['bookshelf_selection_saved']=any(c['tool']=='browse_bookshelf' and json.loads(c['body']).get('ui_action',{}).get('book_ids') for c in calls)
    if case['name']=='webpage':checks['page_read_and_cited']=any(c['tool']=='read_webpage' for c in calls) and 'https://agilemanifesto.org/principles.html' in (turns[0]['result'] or '')
    if case['name']=='artifact':checks['one_saved_artifact']=len(artifacts)==1 and artifacts[0]['title']=='Comparison demo'
    if case['name']=='knowledge':checks['source_note_present']='source' in (turns[0]['result'] or '').lower()
    result={'engine':engine,'case':case['name'],'status':status,'seconds':round(elapsed,2),'model_calls':len(ends),
            'prompt_tokens':sum(m.get('prompt_eval_count') or 0 for m in ends),'output_tokens':sum(m.get('eval_count') or 0 for m in ends),
            'tool_calls':tool_calls,'artifact_count':len(artifacts),'checks':checks,'history':history,
            'initial_context_sha256':case['initial_context_sha256'],'turn':turns[0],
            'model_metrics':ends,'artifacts':artifacts}
    result['model_attempts']=len(starts)
    result['retried_model_activity']=any(m.get('activity_attempt',1)>1 for m in starts)
    return result


async def compare(case,case_file,engines,recovery):
    from temporalio.deepagents import DeepAgentsPlugin
    client=await Client.connect(case['temporal_address'],plugins=[DeepAgentsPlugin()])
    log_path=case_file.parent/'worker.log';log=log_path.open('a');log_path.chmod(0o600)
    worker=worker_start(case_file,log);results=[]
    try:
        await asyncio.sleep(2)
        for engine in engines:
            await wait_idle()
            if worker.poll() is not None:raise RuntimeError('Comparison worker failed; private worker.log contains diagnostics.')
            started=time.monotonic();workflow='BaselineComparison' if engine=='baseline' else 'DeepAgentsComparison'
            handle=await client.start_workflow(workflow,case['id'],id=f'compare-{engine}-{case["id"]}',task_queue=case['task_queue'],execution_timeout=timedelta(minutes=15))
            result_task=asyncio.create_task(handle.result());restarted=False
            while not result_task.done():
                await asyncio.sleep(2)
                if recovery and engine=='deepagents' and not restarted:
                    hist=await history_metrics(handle)
                    if not hist['finished'] and hist['completed'].get('deepagents.invoke_tool',0) and hist['scheduled'].get('deepagents.invoke_model',0)>hist['completed'].get('deepagents.invoke_model',0) and hist['scheduled'].get('deepagents.invoke_model',0)>=2:
                        worker.kill();worker.wait();await asyncio.sleep(1);worker=worker_start(case_file,log);restarted=True
                        print('Recovery probe: restarted only the isolated comparison worker after a completed tool Activity.',flush=True)
            status='completed'
            try:await result_task
            except Exception:status='failed'
            hist=await history_metrics(handle)
            result=summarize(case,engine,time.monotonic()-started,status,hist);result['worker_restarted']=restarted
            result['recovery_verified']=restarted and hist['max_activity_attempt'].get('deepagents.invoke_model',0)>1 and hist['completed'].get('deepagents.invoke_tool')==1
            write_json(case_file.parent/(engine+'.json'),result);results.append(result)
            print(json.dumps({k:result[k] for k in ('case','engine','status','seconds','model_calls','checks')}),flush=True)
    finally:
        worker.terminate()
        try:worker.wait(timeout=10)
        except subprocess.TimeoutExpired:worker.kill();worker.wait()
        log.close()
    return results


async def main():
    load_dotenv(ROOT/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',nargs='+',choices=list(CASES),default=list(CASES))
    parser.add_argument('--only',choices=['baseline','deepagents'])
    parser.add_argument('--recovery',action='store_true')
    args=parser.parse_args()
    # Never modify the serving environment, project dependencies or .env.
    if 'deepagents' not in sys.executable:raise ValueError('Run with the isolated Deep Agents environment, not the site environment.')
    root=application_home()/'experiments/deepagents'/uuid.uuid4().hex;root.mkdir(parents=True,mode=0o700)
    print('Private comparison results: '+str(root),flush=True)
    results=[]
    for index,name in enumerate(args.cases):
        print('Preparing matched context for '+name,flush=True)
        case,path=await asyncio.to_thread(prepare,name,root)
        engines=[args.only] if args.only else (['baseline','deepagents'] if index%2==0 else ['deepagents','baseline'])
        results.extend(await compare(case,path,engines,args.recovery and name=='bookshelf'))
    report={'results':results,'controls':['same configured local model/options','same frozen initial context and application prompt','same scoped application tools; Deep Agents built-ins blocked','isolated queues, stores, logs and artifacts','sequential execution while site idle'],
            'limitations':['small smoke suite, not a statistical performance benchmark','Deep Agents intrinsic middleware/prompt remains part of the candidate','shared local model hardware can affect visitor latency','live brain tool queries can observe new corpus imports','candidate model/tool payloads are recorded by the local Temporal development server']}
    write_json(root/'report.json',report)
    from experiments.comparison_report import render
    (root/'comparison.html').write_text(render(report));(root/'comparison.html').chmod(0o600)
    print('Comparison report: '+str(root/'report.json'),flush=True)


if __name__=='__main__':asyncio.run(main())
