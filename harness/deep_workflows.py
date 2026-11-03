"""Deterministic agent orchestration; all I/O happens in Temporal Activities."""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from deepagents import create_deep_agent
    from langchain.agents.middleware import wrap_model_call, wrap_tool_call
    from langchain_core.messages import AIMessage, ToolMessage, messages_from_dict, messages_to_dict
    from harness.agent import final_answer, finish_bounded_research
    from harness.deep_runtime import routed_tools


async def save_messages(messages):
    await workflow.execute_activity('portfolio.deep.checkpoint', messages_to_dict(messages),
        start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=3))


def scoped_middleware(names):
    allowed = frozenset(names)

    @wrap_model_call
    async def scoped_model(request, handler):
        await save_messages(request.messages)
        tools = [t for t in request.tools if (t.get('name') if isinstance(t, dict) else t.name) in allowed]
        request = request.override(tools=tools)
        request = finish_bounded_research.wrap_model_call(request, lambda value: value)
        return await handler(request)

    @wrap_tool_call
    async def scoped_tool(request, handler):
        if request.tool_call['name'] not in allowed:
            return ToolMessage(content='This tool is unavailable. Use the application tools provided.', tool_call_id=request.tool_call['id'])
        # Preserve a pending tool call if all Activity retries are exhausted and
        # the owner explicitly retries with a new workflow generation.
        await save_messages(request.state['messages'])
        return await handler(request)

    return [scoped_model, scoped_tool]


async def resume_pending_tools(messages, tools):
    # Deep Agents patches dangling calls as canceled at graph entry. Explicit
    # retry must instead finish those calls before entering a fresh graph.
    last = next((i for i in range(len(messages)-1, -1, -1) if isinstance(messages[i], AIMessage)), None)
    if last is None:
        return messages
    resolved = {m.tool_call_id for m in messages[last+1:] if isinstance(m, ToolMessage)}
    catalog = {t.name: t for t in tools}
    for call in messages[last].tool_calls:
        if call['id'] in resolved:
            continue
        if call['name'] in catalog:
            result = await catalog[call['name']].ainvoke(call)
        else:
            result = ToolMessage(content='This tool is unavailable.', tool_call_id=call['id'])
        messages = [*messages, result]
        await save_messages(messages)
    return messages


@workflow.defn
class DeepAgentTurnV1:
    @workflow.run
    async def run(self, ident: str) -> str:
        data = await workflow.execute_activity('portfolio.deep.prepare', ident,
            start_to_close_timeout=timedelta(minutes=3), retry_policy=RetryPolicy(maximum_attempts=3))
        if data['completed']:
            return ident
        tools = routed_tools(data['tool_names'])
        agent = create_deep_agent(model=data['model'], tools=tools,
            system_prompt=data['system_prompt'], middleware=scoped_middleware(data['tool_names']))
        messages = messages_from_dict(data['transcript']) if data['transcript'] else data['messages']
        if data['transcript']:
            messages = await resume_pending_tools(messages, tools)
        output = await agent.ainvoke({'messages': messages}, config={'recursion_limit': 24})
        answer = final_answer(output['messages'][-1].content, data['provider'])
        await workflow.execute_activity('portfolio.deep.complete', answer,
            start_to_close_timeout=timedelta(seconds=30), retry_policy=RetryPolicy(maximum_attempts=3))
        return ident
