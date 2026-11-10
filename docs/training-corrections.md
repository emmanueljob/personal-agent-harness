# Teach the local model through chat corrections

Open a conversation on the site while signed into Owner studio. On a completed
answer, choose **Correct this reply / teach the twin**.

- Write the short correction: what was wrong, or what you want next time.
- Optionally fill **Better answer** with the complete answer you wanted. Saving
  approves this exact text as a future local training target.
- Use tags such as `voice`, `agents`, or `gtm`. Use `always` for a general preference.
- Choose **Save & use privately**. A short correction becomes memory for new
  private turns. Saving a better answer also adds a training candidate. It does
  not change model weights.

The original visible conversation is captured with the correction, so a hosted
example survives note synchronization even if its conversation database isn't on
the Mac. The snapshot includes earlier completed user/assistant messages and the
original answer. It does not include system prompts or tool traces. Conversations
with earlier unfinished/failed messages are excluded from training export rather
than silently training on missing context. For research tasks, put evidence needed
for a self-contained answer in the conversation before approving a rewrite.

In **Admin portal → Learning studio → Corrections**, revisit examples or choose
**Download training examples**. The authenticated `/api/owner/training-data`
endpoint returns the same private JSON dataset with source revision, original
response, correction, approved messages, and a stable train/evaluation split.
Examples from one conversation always share a split; start separate chats for
separate tasks. Small datasets can have an empty split. Review candidates before
training; the split is not a quality score. No training job runs automatically.

Only active, owner-confirmed corrections with an approved complete answer and a
captured complete exchange are eligible. Old notes without an exchange and short
corrections remain memory; no synthetic answer is invented. Editing replaces the
candidate at the current revision; clearing Better answer removes its eligibility.
Forgetting a note removes its future export, but cannot erase downloaded copies
or undo any already-trained adapter.

Publishing a correction publishes only the note and its normal metadata. Original
conversations and approved training answers stay in the private brain. Private
learning records follow the existing owner-controlled Mac/hosted synchronization;
public brain bundles never include the private records. Keep downloaded datasets
outside the source checkout. They are not public code or website assets.

This change adds optional JSON fields to private learning records, with defaults
for older records. No SQLite migration, dependency changes, Temporal workflow
changes, model changes, or automatic training are involved. Older app versions may
ignore/drop these optional fields when editing: keep both studios on the updated
code before collecting examples across them.
