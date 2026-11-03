# Source notes and owner feedback

The local owner studio has a **Reading & listening** tab. It lists canonical books
and active podcast references, with title/author search, tag filters and a notes-only
view. Imported material is availability, not evidence of reading, listening or agreement.

Select a source, record reading/listening status, add tags and explain a specific
agreement, disagreement or influence. **Save & use privately** activates that reviewed
note for new private turns. **Review for public agent** shows the exact saved version;
**Approve public version** copies it to the local public snapshot. Unpublished edits
never change the public version. Deploy the snapshot to update the hosted site.

A completed reply in the local edition has **Correct this reply / teach the twin**.
Write the corrected fact or perspective and save it privately. It uses the same
explicit public review flow. General voice preferences can have the `always` tag;
other corrections are retrieved by topic. Corrections apply to new turns, while a
retry retains its original immutable execution context.

In private chat the agent can call `propose_memory` after an explicit owner correction.
This produces a private draft, ignored by retrieval until the owner reviews and saves
it in the studio. The tool cannot activate or publish a note. Repeating the same durable
tool call returns the existing proposal without replacing subsequent owner edits.
Public conversations never have this tool, and all owner endpoints return 404 on a
public-only deployment, including to callers holding an owner token.

## Storage and retrieval

Versioned `learning.json` files live in the separate public/private brain scopes,
never the repository. Writes use a cross-process file lock and atomic replacement.
Revisions reject stale edits and stale publication approvals. Private retrieval uses
the private version of a given note; public retrieval reads only its public snapshot.
The source catalog returns metadata rather than full transcripts and never traverses
raw source folders. The public exporter includes only the approved public learning file.

Both FileBrain and GBrain retrieve owner-reviewed notes directly from canonical files,
so new corrections do not depend on indexing. Drafts are excluded. Relevant notes carry
owner provenance, source links, reading/listening status and specific perspective; this
does not establish endorsement of the whole source. A recorded correction takes priority
over conflicting older general notes, as knowledge rather than executable instructions.

The shared ranking step deduplicates transcript chunks and reserves a slot for a
relevant book influence when podcasts would otherwise fill the results. Existing
curated book research is under `wiki/influences/`; title/provenance stay beside its
excerpt. Book evidence precedes long transcripts within the bounded context budget.
The prompt asks for relevant books alongside podcasts, attributed by title and author,
using recorded source/shelf links when available. Unrelated answers need no citation.

**Make private** withdraws a local public note. **Forget** removes both local versions;
it does not remove the underlying source. Redeploy to withdraw a previously published
note from production. Existing conversation history and saved execution snapshots retain
what they originally saw; forgetting a note is not deletion of chat history.

There is no weight update, fine-tuning or automatic training on visitor chats. The
improvement comes from explicit, reviewable knowledge updates. Source availability,
consumption, agreement and publication are separate facts.

No dependency, database-schema or workflow-definition changes are required. The new
private tool is registered through the existing Temporal tool-activity adapter. Test
coverage uses synthetic sources and fake models, including scope isolation, revision
conflicts, durable proposal replay and unchanged retry snapshots.
