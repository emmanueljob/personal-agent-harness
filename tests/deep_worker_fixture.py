"""Offline production-workflow worker for recovery integration tests."""
import asyncio
import os
from pathlib import Path
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from temporalio.deepagents.testing import FakeModel
from harness import deep_runtime
from harness.temporal_worker import main


class RecoveryModel(FakeModel):
    async def _agenerate(self, messages, **kwargs):
        if os.getenv('PUBLIC_LATENCY_FIXTURE'):
            await asyncio.sleep(.05)
            message = AIMessage(content='Synthetic public answer')
        elif os.getenv('TOOL_SEQUENCE_FIXTURE'):
            calls = [
                dict(name='read_job_messages', args={'query': 'Synthetic Company'}),
                dict(name='read_job_workspace', args={'query': 'Synthetic Company'}),
                dict(name='cloud_browser_status', args={}),
            ]
            completed = [m for m in messages if isinstance(m, ToolMessage)]
            if len(completed) < len(calls):
                call = calls[len(completed)]
                message = AIMessage(content='', tool_calls=[dict(**call, id='sequence-'+str(len(completed)))])
            else:
                import json
                assert 'Synthetic Company' in completed[0].content
                assert 'Synthetic Company' in completed[1].content
                assert json.loads(completed[-1].content)['state'] == 'synthetic-ready'
                message = AIMessage(content='Synthetic company lookup completed')
        elif any(isinstance(m, ToolMessage) for m in messages):
            if os.getenv('CRASH_MARKER'):
                Path(os.environ['CRASH_MARKER']).write_text('completed tool; model entered')
                await asyncio.sleep(3600)
            message = AIMessage(content='Recovered durable Deep Agents turn')
        else:
            message = AIMessage(content='', tool_calls=[dict(name='save_artifact', args=dict(title='Recovery fixture', markdown='Durable fixture output'), id='fixture-call')])
        return ChatResult(generations=[ChatGeneration(message=message)])


def provide(name):
    deep_runtime.binding()
    return RecoveryModel(responses=['unused'])


if os.getenv('TOOL_SEQUENCE_FIXTURE'):
    from harness import cloud_browser
    async def browser_status(path, body=None):
        assert path == '/status' and body is None
        return {'state': 'synthetic-ready'}
    cloud_browser.request = browser_status

deep_runtime.provide_model = provide
asyncio.run(main())
