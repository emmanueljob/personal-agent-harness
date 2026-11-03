import asyncio
from types import SimpleNamespace
from temporalio.api.failure.v1 import Failure, ApplicationFailureInfo
from temporalio.api.history.v1 import HistoryEvent, WorkflowExecutionFailedEventAttributes
from temporalio.client import WorkflowExecutionStatus
from harness.failures import public_failure, GENERIC, QUOTA
from harness.durable import dispatch_once
from harness.store import Store


def failure(kind,message,cause=None):
    value=Failure(message=message,application_failure_info=ApplicationFailureInfo(type=kind))
    if cause:value.cause.CopyFrom(cause)
    return value


def test_safe_failure_messages_never_expose_raw_provider_data():
    quota=failure('ActivityError','request contains PRIVATE CANARY',failure('OpenAIRateLimitError','429 no credits remaining'))
    assert public_failure(quota)==QUOTA
    assert public_failure(failure('RateLimitError','temporary load, PRIVATE CANARY'))=='The AI service is busy. Please try again shortly.'
    assert public_failure(failure('UnknownError','PRIVATE CANARY'))==GENERIC


def test_dispatcher_persists_classified_failure_and_preserves_completed_turns(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME','legacy')
    store=Store(tmp_path)
    ident=store.enqueue(store.thread('public')['id'],'synthetic question')
    event=HistoryEvent(workflow_execution_failed_event_attributes=WorkflowExecutionFailedEventAttributes(failure=failure('RateLimitError','insufficient_quota PRIVATE CANARY')))
    class Handle:
        async def describe(self):return SimpleNamespace(status=WorkflowExecutionStatus.FAILED)
        async def fetch_history(self):return SimpleNamespace(events=[event])
    class Client:
        async def start_workflow(self,*a,**kw):return Handle()
    asyncio.run(dispatch_once(Client(),store))
    assert store.query('SELECT status,error FROM turns WHERE id=?',(ident,))[0]=={'status':'failed','error':QUOTA}
