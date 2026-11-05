# Source notes and owner feedback

Both the local and hosted owner studios have a **Reading & listening** tab. The hosted studio uses a separate `OWNER_STUDIO_TOKEN`, configured outside Git, as an owner password. It grants access only to source annotations and corrections; private profiles, private chat, routines and the full private library remain unavailable on the public deployment. It lists canonical books
and active podcast references, with title/author search, tag filters and a notes-only
view. Imported material is availability, not evidence of reading, listening or agreement.

Select a source, record reading/listening status, add tags and explain a specific
agreement, disagreement or influence. **Save & use privately** activates that reviewed
note for new private turns. **Review for public agent** shows the exact saved version;
**Approve public version** copies it to the public snapshot on that instance. Unpublished edits
never change the public version. New turns on that instance can retrieve the approved note immediately.

A completed reply in the local edition, or a hosted reply after entering the owner password, has **Correct this reply / teach the twin**.
Write the corrected fact or perspective and save it privately. It uses the same
explicit public review flow. General voice preferences can have the `always` tag;
other corrections are retrieved by topic. Corrections apply to new turns, while a
retry retains its original immutable execution context.

In private chat the agent can call `propose_memory` after an explicit owner correction.
This produces a private draft, ignored by retrieval until the owner reviews and saves
it in the studio. The tool cannot activate or publish a note. Repeating the same durable
tool call returns the existing proposal without replacing subsequent owner edits.
Public conversations never have this tool. Hosted annotation endpoints require the studio password. Private-chat/profile/routine endpoints still return 404 even with that password.

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
it does not remove the underlying source. Deploy to synchronize a withdrawal between the Mac and production. Existing conversation history and saved execution snapshots retain
what they originally saw; forgetting a note is not deletion of chat history.

There is no weight update, fine-tuning or automatic training on visitor chats. The
improvement comes from explicit, reviewable knowledge updates. Source availability,
consumption, agreement and publication are separate facts.

No dependency, database-schema or workflow-definition changes are required. The new
private tool is registered through the existing Temporal tool-activity adapter. Test
coverage uses synthetic sources and fake models, including scope isolation, revision
conflicts, durable proposal replay and unchanged retry snapshots.

## Deployment synchronization

The private owner release controller performs a three-way merge of local and hosted
public/private **learning notes only** before publishing the brain snapshot. A private
baseline outside Git tracks the last observed common state. New notes, one-sided edits
and deletions merge automatically; different edits to the same note stop the release.
Full private profiles, email, raw sources and chat history are never synchronized.

Synchronization is at deploy time, not continuous. A file lock guards local edits and
an expected-state checksum guards the remote application step. If hosted notes change
during transfer, publication fails without replacing them. The next release can retry
against the latest state. Baselines and transfers stay outside the repository/image.
