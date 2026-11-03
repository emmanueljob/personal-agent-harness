# Deep Agents runtime

`AGENT_RUNTIME=deepagents` with `EXECUTOR=temporal` selects the new runtime for newly queued turns, including routine turns. Existing turns are migrated to `legacy`; their model/profile snapshots and LangGraph SQLite checkpoints remain intact. Routing is written atomically with the queued turn and survives configuration changes and explicit retries.

The existing SQLite outbox submits `DeepAgentTurnV1` on a separate `-deepagents-v1` queue. The official Temporal plugin (pinned `temporalio-deepagents==0.0.3`, `deepagents==0.7.32`) runs model calls and trusted application tools as separate Activities. Workflow replay reuses completed Activity results. Preparation freezes the same scoped brain, prompt, history, model and context settings used by the legacy executor. Inference remains on the configured local Ollama model; there is no cloud fallback.

The worker binds each Activity's workflow ID to its queued turn. Tool arguments cannot choose another turn, scope or write destination. Both model-visible schemas and worker-side tool invocation enforce the catalog: public knowledge, web research and bookshelf selection; private mode additionally permits saving artifacts. Deep Agents' native filesystem, shell, planning and subagent tools are filtered out and blocked if directly requested. No host filesystem backend or arbitrary MCP connection is exposed.

Before model/tool calls, a separate Activity saves the original message transcript outside Git. A failed workflow can be explicitly retried with a new generation, using that transcript, including pending tool calls. A successful tool may repeat if its result was not yet snapshotted when the workflow failed; current writes use artifact upserts and web caches. Temporal provides at-least-once Activity execution, so future external-write plugins must implement idempotency. Model/tool retry limits are bounded; this does not guarantee exactly-once inference or effects.

`harness.sqlite` contains turn routing, workflow bindings and retry transcripts; `temporal.sqlite` contains durable Activity history. Legacy `checkpoints.sqlite` is still required. Back up all three together with the canonical external brain. History and transcripts contain scoped conversation data and stay in the local application data directory.

## Rollback

Set `AGENT_RUNTIME=legacy` in the private `.env` and restart web/worker processes. New turns return to the legacy runner; existing Deep Agents turns keep their route and finish on the versioned queue. Both workers remain registered. Do not delete either queue, any runtime database, or the dependency lock to roll back. Deep Agents requires Temporal; the legacy local executor is available with `AGENT_RUNTIME=legacy` and `EXECUTOR=local`.

Public/private source storage, knowledge provider, current model, tool results, routine schedules, artifacts and frontend behavior are unchanged. Dependency upgrades that alter workflow behavior require replay testing or a new workflow/queue version; do not silently upgrade pinned workflow libraries while old work is in flight.
