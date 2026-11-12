# Owner helper delegation

Home uses the configured personal-agent model. It views and manages CRM records through local tools. Its `delegate_work` tool queues research, outreach drafts, explicit sending requests and other non-CRM tasks for Grokbot, Muse or both. Named helpers take precedence. Automatic selection prefers an available Grokbot for research and an available Muse for other work, falling back to the other available helper.

The original chat message, request, helper assignment IDs and final answer remain linked in private SQLite. The tool sends a standalone instruction and the owner's request. Card evidence is opt-in and limited to the relevant selected card. Profiles, unrelated thread history and other cards are excluded from the helper payload. Existing snapshot checks apply when selected-card versions are supplied.

## Runtime requirements

Deploy the app and both normal/deep agent workers together. New Home snapshots enable `delegate_work`, `read_helper_work` and the existing local CRM tool subset. Existing frozen Home snapshots retain their prior tool scope. Workflow definitions and dependency versions are unchanged. Two additive tables, `owner_helper_requests` and `owner_helper_tasks`, retain request and transport bindings in the external application volume.

The existing Grok webhook and callback credentials are reused. Delegations use wire action `career_question` with `task_type: helper_task`. The Bot must claim the assignment and follow the returned instruction. Its existing routine may need the updated guidance shown in the Grok connection panel to permit research and expressly authorized outreach. Connection proof establishes the transport, not every external capability. A helper must report blockers and limitations.

Muse uses the existing paired Mac worker and fixed Muse recipient. Its HTTP/nonce protocol is unchanged. The app and paired companion must be connected as described in `muse-conversation-transport.md`. No new bridge credential is added. The paired worker continues to archive only locally and returns matched task replies.

## Results and recovery

The existing local worker loop and Temporal dispatch loop service queued helper work. Grok uses the existing transactional claim and stable submission protocol. Muse reserves a send before the companion receives it. The app does not automatically resend uncertain assignments. Explicit retries reuse the Grok task ID, and retry initially unavailable work once the helper becomes available. An uncertain Muse send requires inspection of the original paired chat and is never repeated by the retry operation.

The original conversation shows pending, partial, unavailable, failed or uncertain transport states. When all helpers are terminal, the normal worker queues an owner-answer continuation. It is bound to the originating thread and turn, with no tools that could recursively dispatch more work. Reloading or navigating to another card does not retarget it. A failed owner answer can be retried without repeating the helper assignment.

A completed helper task means a report was received. Reports are labeled `helper_report_only` and `deliveryVerified: false`. Helper statements, drafts and transport receipts to a bot do not establish delivery of human outreach. The owner answer must attribute reported work and keep external delivery unverified. Explicit sending requests are passed to the helper with the owner's original wording. Draft-only requests do not authorize sending.

The request and owner-answer IDs are deterministic at retry boundaries. Enqueueing with `request_work(..., connection=db)` participates in the caller's SQLite transaction and performs no network operation. Call `initialize(store)` before that transaction. `read_request(db, id)` and `job_status(..., connection=db)` preserve the caller's snapshot. Owner-only status/retry routes live under `/api/admin/helper-requests`.

## Synthetic verification

`tests/test_helper_delegation.py` uses a fake tool-calling model with the actual agent graph, actual callback routes, real SQLite and a fake Muse companion boundary. It covers local CRM read/write without helpers, automatic and named routing, both helpers, partial results, owner synthesis, reload, duplicate callbacks, concurrent enqueue, explicit unavailable/uncertain retries, outer-transaction rollback and a lost tool-result retry.

Live research, outreach or human delivery is not established by these tests. The API keeps full helper reports private. Model continuations use bounded report excerpts and disclose truncation.
