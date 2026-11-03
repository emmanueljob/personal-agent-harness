from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from deepagents import create_deep_agent
    from experiments.deepagents_runtime import comparison_middleware, routed_tools


@workflow.defn
class BaselineComparison:
    @workflow.run
    async def run(self, case_id: str) -> str:
        return await workflow.execute_activity(
            'comparison.baseline',case_id,start_to_close_timeout=timedelta(minutes=15),
            heartbeat_timeout=timedelta(seconds=20),retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class DeepAgentsComparison:
    @workflow.run
    async def run(self, case_id: str) -> str:
        data=await workflow.execute_activity('comparison.load',case_id,start_to_close_timeout=timedelta(seconds=30))
        agent=create_deep_agent(
            model='ollama:'+data['model'],tools=routed_tools(data['tool_names']),
            system_prompt=data['system_prompt'],middleware=comparison_middleware(),
        )
        output=await agent.ainvoke({'messages':data['messages']},config={'recursion_limit':24})
        content=output['messages'][-1].content
        if isinstance(content,list):content=str(content)
        if '</think>' in content:content=content.rsplit('</think>',1)[-1].strip()
        await workflow.execute_activity('comparison.save',{'case_id':case_id,'answer':content},start_to_close_timeout=timedelta(seconds=30))
        return case_id
