# Isolated Deep Agents + Temporal comparison

This opt-in experiment runs the existing `harness.agent.execute` code and a Deep Agents candidate against the same configured local Ollama model, frozen initial context/application prompt, canonical brain, and scoped tool schemas. It never routes work to the site's task queue or changes the site's `.env`, dependency environment, UI, profiles, library or artifact store.

Install into a separate ignored environment using your existing `uv` executable:

```sh
uv venv .runtime/experiments/deepagents/.venv --python .venv/bin/python
uv pip freeze --python .venv/bin/python | sed '/^tenacity==/d' > .runtime/experiments/deepagents/comparison-requirements.txt
uv pip install --python .runtime/experiments/deepagents/.venv/bin/python -r .runtime/experiments/deepagents/comparison-requirements.txt temporalio-deepagents==0.0.3 deepagents==0.7.32
.runtime/experiments/deepagents/.venv/bin/python scripts/compare_deepagents.py --cases knowledge bookshelf webpage artifact --recovery
```

Create the experiment directory before redirecting requirements if needed. The existing project packages are frozen in the comparison environment, except `tenacity`: the plugin's provider dependencies currently require an older compatible version. The site's environment is untouched. Capture and compare installed versions before interpreting results. No Deep Agents dependency is added to the site's `pyproject.toml` or lockfile.

Start the site's local Ollama and Temporal development server normally first. The experiment waits for accepted site turns before each run and runs engines sequentially. It shares the local model's hardware, so a visitor arriving during an inference can experience contention. It does not reserve the GPU or claim full compute isolation.

Each case gets a unique Temporal task queue, Workflow IDs, worker process and separate SQLite stores. All case snapshots, logs, answers, metrics and artifacts live under the application data directory at `experiments/deepagents/<run-id>/`, outside Git, with private permissions. Brain searches read the existing canonical source scopes through GBrain, including the explicit same-scope canonical-file fallback during indexing. Tools that write artifacts/results write only into experiment state. No routine schedules are created and no collector/index process is stopped.

Preparation invokes the original executor with only graph construction intercepted: this collects its actual application system prompt and initial execution-context snapshot without making a model call or checkpointing a result. Both engines receive the same serialized snapshot; its SHA-256 is recorded. The baseline then runs the original executor normally inside a heartbeating Temporal Activity. The candidate uses `create_deep_agent` inside a Workflow and the official plugin's `deepagents.invoke_model` / `deepagents.invoke_tool` Activities. A worker-side model factory uses the original `harness.models.local_model` configuration, including context size, temperature, output cap and reasoning setting. Cloud model keys are removed from the experiment worker and unexpected model names/providers are rejected.

This is **matched-tool mode**: only the original application's tools are exposed to the model. Middleware blocks any attempt to invoke additional Deep Agents planning, filesystem, execution or delegation tools. The same existing four-result tool limit/context transformation is applied. Deep Agents' intrinsic middleware remains part of the candidate; this is not an evaluation of its unrestricted planning/subagent/file capabilities. No host filesystem/shell backend is mounted.

Cases check grounded answers, persisted bookshelf selections, a read/cited public page and one private Markdown artifact. Source attribution checks are basic smoke assertions, not a substitute for reviewing answer quality. Both arms use the same dependency environment for the controlled comparison; the serving site remains in its original environment. Timings start at Workflow dispatch, after shared context preparation/retrieval and worker startup. Engine order alternates, but shared model prompt caches still affect latency. Live brain-tool queries and web fetches can observe changes between engines; only initial context is frozen.

`--recovery` kills **only** the comparison worker during the bookshelf candidate after a tool Activity completed and another model Activity was scheduled, then starts a replacement with the same case. Workflow history verifies a retried model Activity and exactly one completed tool Activity. The site's worker, model server, Temporal server and index builds are not killed by this probe. Token totals include completed provider responses, not work lost in an interrupted inference.

The private `report.json` and `comparison.html` retain answers side by side with timing, actual model/tool calls, token metadata, context hashes and Workflow Activity history. This is a small smoke suite, not a statistical benchmark. Per-call Temporal execution records model/tool data in the local development server; use appropriate payload handling before applying this pattern to private hosted data. Existing production turns/checkpoints are not migrated.
