"""Extra checks run only in the opt-in, isolated Deep Agents environment."""
import asyncio
from types import SimpleNamespace
import pytest

pytest.importorskip('temporalio.deepagents',reason='Opt-in isolated Deep Agents environment')
from experiments import deepagents_runtime as runtime
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage,ToolMessage


def test_candidate_rejects_production_state_paths(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'CASE',{'baseline_root':str(tmp_path/'site'/'nested'),'brain_root':str(tmp_path/'brain')})
    monkeypatch.setattr(runtime,'data_directory',lambda:tmp_path/'site')
    with pytest.raises(RuntimeError,match='separate'):runtime.store_for('baseline')
    assert not (tmp_path/'site').exists()


def test_candidate_filters_extra_tools_and_blocks_direct_invocation(monkeypatch):
    monkeypatch.setattr(runtime,'CASE',{'tool_names':['search_knowledge']})
    request=ModelRequest(model=FakeListChatModel(responses=['unused']),messages=[HumanMessage(content='test')],tools=[{'name':'search_knowledge'},{'name':'execute'},{'name':'task'}])
    async def model_handler(value):return value
    result=asyncio.run(runtime.matched_model.awrap_model_call(request,model_handler))
    assert result.tools==[{'name':'search_knowledge'}]
    invoked=[]
    async def tool_handler(value):invoked.append(True)
    blocked=asyncio.run(runtime.matched_tool.awrap_tool_call(SimpleNamespace(tool_call={'name':'execute','id':'probe'}),tool_handler))
    assert isinstance(blocked,ToolMessage) and not invoked


def test_candidate_uses_the_existing_bounded_research_transformation(monkeypatch):
    monkeypatch.setattr(runtime,'CASE',{'tool_names':['search_knowledge']})
    request=ModelRequest(model=FakeListChatModel(responses=['unused']),messages=[HumanMessage(content='question')]+[ToolMessage(content='evidence',tool_call_id=str(i)) for i in range(4)],tools=[{'name':'search_knowledge'}])
    async def handler(value):return value
    result=asyncio.run(runtime.matched_model.awrap_model_call(request,handler))
    assert not result.tools and all(not isinstance(m,ToolMessage) for m in result.messages)


def test_model_factory_reuses_original_configuration_and_rejects_cloud_names(monkeypatch):
    config={'provider':'ollama','model':'local-fixture'}
    monkeypatch.setattr(runtime,'CASE',{'config':config})
    monkeypatch.setattr(runtime.activity,'info',lambda:SimpleNamespace(activity_type='deepagents.invoke_model',attempt=1))
    calls=[];model=SimpleNamespace(callbacks=[])
    monkeypatch.setattr(runtime,'local_model',lambda value:calls.append(value) or model)
    assert runtime.provide_model('ollama:local-fixture') is model and calls==[config]
    assert len(model.callbacks)==1
    with pytest.raises(RuntimeError):runtime.provide_model('openai:other')
