"""Isolated worker: original tools execute against experiment state only."""
import argparse
import asyncio
import contextlib
import json
import os
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from langchain.agents.middleware import wrap_model_call,wrap_tool_call
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from temporalio import activity
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.deepagents import DeepAgentsPlugin,tool_as_activity
from temporalio.worker import Worker

from harness import agent as baseline
from harness.models import local_model
from harness.store import Store
from harness.gbrain import GBrain
from harness.paths import data_directory

CASE = None
ROUTED = {}


def store_for(engine):
    root=Path(CASE[engine+'_root']).resolve()
    if root.is_relative_to(data_directory().resolve()) or root.is_relative_to(Path(CASE['brain_root']).resolve()):
        raise RuntimeError('Experiment stores must be separate from site state and canonical knowledge.')
    store=Store(root)
    store.brain=GBrain(Path(CASE['brain_root']),binary=CASE['gbrain_binary'])
    return store


def metric(engine,kind,body):
    store=store_for(engine)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS comparison_metrics(id INTEGER PRIMARY KEY,kind TEXT,body TEXT,created REAL)')
        db.execute('INSERT INTO comparison_metrics(kind,body,created) VALUES (?,?,?)',(kind,json.dumps(body),time.time()))


class ModelMetrics(BaseCallbackHandler):
    def __init__(self,engine):self.engine=engine;self.attempt=activity.info().attempt

    def on_chat_model_start(self,serialized,messages,**kwargs):
        from langchain_core.messages.utils import count_tokens_approximately
        metric(self.engine,'model_start',{'estimated_message_tokens':count_tokens_approximately(messages[0]),'model':CASE['config']['model'],'activity_attempt':self.attempt})

    def on_llm_end(self,response,**kwargs):
        message=response.generations[0][0].message
        metadata=message.response_metadata
        metric(self.engine,'model_end',{k:metadata.get(k) for k in ('prompt_eval_count','eval_count','total_duration','load_duration','done_reason') }|{'tool_calls':[t['name'] for t in message.tool_calls]})


def build_model(config=None):
    engine='baseline' if activity.info().activity_type=='comparison.baseline' else 'deepagents'
    config=config or CASE['config']
    if config['provider']!='ollama' or config['model']!=CASE['config']['model']:
        raise RuntimeError('Only the configured local comparison model is permitted.')
    model=local_model(config)
    model.callbacks=[ModelMetrics(engine)]
    return model


def provide_model(name):
    if name!='ollama:'+CASE['config']['model']:raise RuntimeError('Unexpected model provider.')
    return build_model(CASE['config'])


def check_case(case_id):
    if case_id!=CASE['id']:raise RuntimeError('Worker only serves its configured comparison case.')


@activity.defn(name='comparison.load')
async def load_case(case_id: str) -> dict:
    check_case(case_id)
    return {'model':CASE['config']['model'],'system_prompt':CASE['system_prompt'],
            'messages':CASE['snapshot']['messages'],'tool_names':CASE['tool_names']}


@activity.defn(name='comparison.save')
async def save_answer(data: dict) -> str:
    check_case(data['case_id'])
    store=store_for('deepagents')
    with store.connect() as db:
        db.execute("UPDATE turns SET status='completed',result=? WHERE id=?",(data['answer'],CASE['deepagents_turn']))
    return CASE['id']


@activity.defn(name='comparison.baseline')
async def run_baseline(case_id: str) -> str:
    check_case(case_id)
    from harness.runner import run_turn
    async def heartbeat():
        while True:activity.heartbeat(case_id);await asyncio.sleep(2)
    beat=asyncio.create_task(heartbeat())
    try:await asyncio.to_thread(run_turn,store_for('baseline'),CASE['baseline_turn'],False)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):await beat
    return case_id


def register_tools():
    store=store_for('deepagents')
    context={'store':store,'mode':CASE['mode'],'thread_id':CASE['deepagents_thread'],
             'turn_id':CASE['deepagents_turn'],'web_enabled':CASE['config']['web_enabled']}
    for original in baseline.plugins.tools(context):
        def invoke(_original=original,**kwargs):
            metric('deepagents','tool',{'name':_original.name})
            store.event(CASE['deepagents_turn'],'tool_start',_original.name)
            return _original.invoke(kwargs)
        proxy=StructuredTool.from_function(invoke,name=original.name,description=original.description,args_schema=original.args_schema)
        ROUTED[original.name]=tool_as_activity(proxy,start_to_close_timeout=timedelta(minutes=3),activity_options={'retry_policy':RetryPolicy(maximum_attempts=2)})


def routed_tools(names):
    if sorted(names)!=sorted(ROUTED):raise RuntimeError('Matched tool catalog changed.')
    return [ROUTED[name] for name in names]


@wrap_model_call
async def matched_model(request,handler):
    tools=[tool for tool in request.tools if (tool.get('name') if isinstance(tool,dict) else tool.name) in CASE['tool_names']]
    request=request.override(tools=tools)
    request=baseline.finish_bounded_research.wrap_model_call(request,lambda value:value)
    return await handler(request)


@wrap_tool_call
async def matched_tool(request,handler):
    if request.tool_call['name'] not in CASE['tool_names']:
        return ToolMessage(content='This comparison exposes only the matched application tool set.',tool_call_id=request.tool_call['id'])
    return await handler(request)


def comparison_middleware():return [matched_model,matched_tool]


async def main():
    global CASE
    parser=argparse.ArgumentParser();parser.add_argument('--case-file',type=Path,required=True);args=parser.parse_args()
    CASE=json.loads(args.case_file.read_text())
    os.environ['OLLAMA_CONTEXT']=str(CASE['context_window'])
    os.environ['OLLAMA_BASE_URL']=CASE['ollama_base_url']
    os.environ['MODEL_PROVIDER']='ollama'
    os.environ['WEB_SEARCH_PROVIDER']=CASE['web_provider']
    for key in list(os.environ):
        if any(word in key for word in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY','TAVILY_API_KEY','LANGSMITH_API_KEY')):os.environ.pop(key,None)
    os.environ['LANGSMITH_TRACING']='false'
    baseline.local_model=build_model
    register_tools()
    from experiments.deepagents_workflows import BaselineComparison,DeepAgentsComparison
    plugin=DeepAgentsPlugin(model_provider=provide_model,
        model_activity_options={'start_to_close_timeout':timedelta(minutes=5),'heartbeat_timeout':timedelta(seconds=20),'retry_policy':RetryPolicy(maximum_attempts=2)},
        passthrough_modules=['experiments.deepagents_runtime','harness.agent'])
    client=await Client.connect(CASE['temporal_address'],plugins=[plugin])
    worker=Worker(client,task_queue=CASE['task_queue'],workflows=[BaselineComparison,DeepAgentsComparison],
                  activities=[load_case,save_answer,run_baseline],max_concurrent_activities=1,max_heartbeat_throttle_interval=timedelta(seconds=2))
    print('Comparison worker ready',flush=True)
    await worker.run()


if __name__=='__main__':
    # Import under its canonical name so the sandbox shares configured globals.
    import experiments.deepagents_runtime as runtime
    asyncio.run(runtime.main())
