# Personal Agent Harness Restart Benchmark

When a scheduled agent is stopped while waiting for approval or writing to a downstream service, can it finish the same task after restart? This benchmark runs the Personal Agent Harness scheduler, outbox, Deep Agents workflow, and checkpoint code against a dedicated Temporal development server. A deterministic model summarizes a synthetic library notice. A fixture reviewer approves or declines that exact digest, and a separate SQLite database records mock downstream writes.

## Reproduce the run

From the repository root, install the locked Python dependencies with `uv sync --locked`. Run the benchmark with a local Temporal CLI executable. It starts its own loopback server and workers in temporary directories, with stubbed model and tool providers. No model keys, app configuration, or existing Temporal server are needed.

```sh
.venv/bin/python -m experiments.restart_benchmark \
  --temporal-cli temporal --trials 3 --output /tmp/harness-restart-report.json
```

Use `--case after_write` to run the lost-acknowledgment scenario by itself. Repeat `--case` to select multiple scenarios. Omit `--output` to write JSON to stdout. The process returns a nonzero exit code if a measured check fails. The report records the Git revision, runtime versions, activity attempts, the approved decision, effective writes, and a timestamped event trace for each trial.

## Fault points and expected results

| Scenario | Interruption | Expected result |
| --- | --- | --- |
| Approved control | The worker stays up throughout | One write attempt, one effect |
| Before approval | The review is pending when the worker is killed. The reviewer approves while it is down. | The approval activity retries, the decision remains valid, and one effect is recorded |
| After approval | The reviewer approved and the write activity started, but the sink has received nothing | The write activity retries and one effect is recorded |
| After the write | The sink committed, then the worker stopped before returning a receipt to Temporal | The sink receives two attempts and returns the same receipt both times, with one effective write |
| Declined after restart | The review is pending when the worker is killed. The reviewer declines while it is down. | The workflow completes with the decline and makes zero write attempts |
| Lost acknowledgment without deduplication | The same post-commit interruption occurs with deduplication disabled at the sink | The sink records two effects, demonstrating that the retry reached it twice |

The runner manually triggers one real Temporal Schedule per case and verifies that exactly one turn enters the queue. Before killing a worker it checks the planned fault boundary against the persisted decision and mock sink. The fake model checks that the scheduled prompt and source are still present, and the runner checks the final decision and receipt against the journal. The mock service uses a key derived from the scheduled turn ID and the digest hash to deduplicate retries. It also checks that the approval matches that digest before accepting a write.

## Results on November 21, 2026

All 18 trials met their expected checks at revision `6ebcca32`, using Python 3.12.16, Temporal Server 1.32.0, and Temporal Python SDK 1.35.0 on macOS arm64. Each row represents three trials with identical write counts.

| Scenario | Sink attempts per trial | Effective writes per trial | Median restart to completion |
| --- | ---: | ---: | ---: |
| Approved control | 1 | 1 | — |
| Before approval | 1 | 1 | 11.123 s |
| After approval | 1 | 1 | 11.083 s |
| After the write | 2 | 1 | 11.085 s |
| Declined after restart | 0 | 0 | 11.088 s |
| Lost acknowledgment without deduplication | 2 | 2 | 11.082 s |

The post-commit crash made the sink see the same operation twice in every trial. With the idempotency key enabled, both attempts returned the first receipt. With the key disabled, the two attempts returned separate receipts and left two records. The approval was read once in both variants, as confirmed by the Temporal Activity history.

## Scope of the results

The approval inbox and downstream sink are benchmark fixtures. The approval activity polls the fixture inbox, and the test tool activities use a 10-second start-to-close timeout to make failures quick to reproduce. The server and fixture databases stay up while the agent worker dies. Elapsed time measures recovery under those settings. The experiments cover one fault per trial and the approved or declined synthetic action. Applying the same approach to a production integration requires checking which actions accept idempotency keys, where the approval is persisted, and what happens when the host or database also fails. [Temporal workflow replay](https://docs.temporal.io/workflows) · [Temporal Signals and Updates](https://docs.temporal.io/encyclopedia/workflow-message-passing)
