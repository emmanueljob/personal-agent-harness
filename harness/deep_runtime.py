"""Worker-side bindings for the versioned Deep Agents Temporal runtime.

Only application tools are exposed. Workflow IDs, not model arguments, select
credentials, knowledge visibility and the destination for writes.
"""
import asyncio
import json
from datetime import timedelta

from langchain_core.tools import StructuredTool
from temporalio import activity
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.deepagents import DeepAgentsPlugin, tool_as_activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from .agent import ActivityEvents, build_system_prompt, plugins, prepare_turn
from .models import local_model, openai_options
from .store import Store

ROUTED = {}


def binding():
    store = Store()
    wid = activity.info().workflow_id
    rows = store.query('SELECT turn_id,runtime FROM workflow_turns WHERE workflow_id=?', (wid,))
    if not rows or rows[0]['runtime'] != 'deepagents':
        raise ApplicationError('Unknown execution binding.', non_retryable=True)
    ident = rows[0]['turn_id']
    if store.workflow_id(ident) != wid or store.runtime_for(ident) != 'deepagents':
        raise ApplicationError('Stale execution binding.', non_retryable=True)
    turn = store.query('SELECT * FROM turns WHERE id=?', (ident,))[0]
    mode = store.query('SELECT mode FROM threads WHERE id=?', (turn['thread_id'],))[0]['mode']
    return store, turn, mode


def context(store, turn, mode, config):
    return dict(store=store, mode=mode, thread_id=turn['thread_id'], turn_id=turn['id'], web_enabled=config['web_enabled'], home_agent=config.get('home_agent', False), home_delegation=config.get('home_delegation', False))


def saved_config(store, turn):
    return json.loads(store.query('SELECT body FROM execution_context WHERE turn_id=?', (turn['id'],))[0]['body'])['config']


@activity.defn(name='portfolio.deep.prepare')
async def prepare(ident: str) -> dict:
    return await asyncio.to_thread(_prepare, ident)


def _prepare(ident):
    store, turn, mode = binding()
    if ident != turn['id']:
        raise ApplicationError('Invalid execution binding.', non_retryable=True)
    if turn['status'] == 'completed':
        return {'completed': True}
    with store.connect() as db:
        db.execute("UPDATE turns SET status='running',error=NULL WHERE id=?", (ident,))
    mode, snapshot = prepare_turn(store, turn)
    transcript = store.query('SELECT body FROM deep_transcripts WHERE turn_id=?', (ident,))
    store.event(ident, 'runtime', 'Deep Agents · Temporal per-call execution')
    return dict(completed=False, model=snapshot['config']['provider']+':'+snapshot['config']['model'],
                provider=snapshot['config']['provider'], system_prompt=build_system_prompt(snapshot, mode),
                messages=snapshot['messages'], transcript=json.loads(transcript[0]['body']) if transcript else None,
                tool_names=[t.name for t in plugins.tools(context(store, turn, mode, snapshot['config']))])


@activity.defn(name='portfolio.deep.checkpoint')
async def checkpoint(messages: list) -> None:
    await asyncio.to_thread(_checkpoint, messages)


def _checkpoint(messages):
    store, turn, _ = binding()
    with store.connect() as db:
        db.execute('INSERT INTO deep_transcripts VALUES (?,?) ON CONFLICT(turn_id) DO UPDATE SET body=excluded.body',
                   (turn['id'], json.dumps(messages)))


@activity.defn(name='portfolio.deep.complete')
async def complete(answer: str) -> None:
    await asyncio.to_thread(_complete, answer)


def _complete(answer):
    store, turn, _ = binding()
    with store.connect() as db:
        db.execute("UPDATE turns SET status='completed',result=?,error=NULL WHERE id=? AND status!='completed'", (answer, turn['id']))


def provide_model(name):
    store, turn, mode = binding()
    config = saved_config(store, turn)
    from .public_access import reserve_model_call
    reserve_model_call(store,mode)
    if name != config['provider']+':'+config['model']:
        raise ApplicationError('Unexpected model configuration.', non_retryable=True)
    if config['provider'] == 'ollama':
        model = local_model(config)
    elif config['provider'] == 'openai':
        from langchain_openai import ChatOpenAI
        model = ChatOpenAI(**openai_options(config))
    else:
        raise ApplicationError('Unsupported model provider.', non_retryable=True)
    model.callbacks = [ActivityEvents(store, turn['id'])]
    return model



class DeferredModel:
    """Build the bound provider off-loop when the SDK starts async model I/O."""
    def __init__(self, name, bindings=()):
        self.name = name
        self.bindings = bindings

    def bind_tools(self, tools):
        return DeferredModel(self.name, (*self.bindings, ('bind_tools', tools)))

    def bind(self, **kwargs):
        return DeferredModel(self.name, (*self.bindings, ('bind', kwargs)))

    async def _model(self):
        model = await asyncio.to_thread(provide_model, self.name)
        for operation, value in self.bindings:
            model = model.bind_tools(value) if operation == 'bind_tools' else model.bind(**value)
        return model

    async def ainvoke(self, messages, config=None):
        model = await self._model()
        return await model.ainvoke(messages, config=config)

    async def astream(self, messages, config=None):
        model = await self._model()
        async for chunk in model.astream(messages, config=config):
            yield chunk


def tool_binding():
    store, turn, mode = binding()
    tools = {t.name: t for t in plugins.tools(context(store, turn, mode, saved_config(store, turn)))}
    return store, turn, tools


async def invoke_tool(name, kwargs):
    store, turn, tools = await asyncio.to_thread(tool_binding)
    if name not in tools:
        raise ApplicationError('Tool unavailable for this execution.', non_retryable=True)
    events = ActivityEvents(store, turn['id'])
    await asyncio.to_thread(events.on_tool_start, {'name': name}, '')
    result = await tools[name].ainvoke(kwargs)
    await asyncio.to_thread(events.on_tool_end, result)
    return result


def register_tools():
    # Factories only construct schemas here. They do not access knowledge or run
    # any tool. Actual calls rebuild the catalog from their Activity binding.
    base = dict(mode='private', store=None, thread_id='', turn_id='', web_enabled=True)
    catalog = plugins.tools(base) + plugins.tools({**base, 'home_agent': True, 'home_delegation': True})
    for original in {t.name: t for t in catalog}.values():
        async def invoke(_name=original.name, **kwargs):
            return await invoke_tool(_name, kwargs)
        proxy = StructuredTool.from_function(coroutine=invoke, name=original.name,
                                            description=original.description, args_schema=original.args_schema)
        ROUTED[original.name] = tool_as_activity(proxy, start_to_close_timeout=timedelta(minutes=3),
            activity_options={'retry_policy': RetryPolicy(maximum_attempts=2)})


def routed_tools(names):
    return [ROUTED[name] for name in names]


async def make_worker(address, queue):
    register_tools()
    from .deep_workflows import DeepAgentTurnV1
    plugin = DeepAgentsPlugin(model_provider=DeferredModel,
        model_activity_options={'start_to_close_timeout': timedelta(minutes=5),
                                'heartbeat_timeout': timedelta(seconds=20),
                                'retry_policy': RetryPolicy(maximum_attempts=2)},
        passthrough_modules=['harness.deep_runtime', 'harness.deep_workflows', 'harness.agent'])
    client = await Client.connect(address, plugins=[plugin])
    return Worker(client, task_queue=queue, workflows=[DeepAgentTurnV1],
                  activities=[prepare, checkpoint, complete], max_concurrent_activities=1,
                  max_heartbeat_throttle_interval=timedelta(seconds=2))
