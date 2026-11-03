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
        if any(isinstance(m, ToolMessage) for m in messages):
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


deep_runtime.provide_model = provide
asyncio.run(main())
