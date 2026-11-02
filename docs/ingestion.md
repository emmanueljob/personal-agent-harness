# External knowledge ingestion contract (v1)

The importer accepts one **whole source** per JSONL line. It owns validation, stable source storage, content hashes, revisions, retrieval chunks and index synchronization. Collectors own discovery, transcription and cross-feed/source deduplication. All input text is untrusted external reference material, never an owner belief or endorsement.

`scripts/ingest_brain.py` defaults to private scope and a read-only preview. It performs no collection, network fetches, model calls, summarization or automatic publication. Credentials/configuration are loaded from the checkout's ignored `.env`. Source material must stay outside the checkout.

```sh
# Preview the collector's full-source JSONL export.
.venv/bin/python scripts/ingest_brain.py --input /external/path/brain-import.jsonl --format podcast --collection podcasts

# Apply canonical records, then synchronize the private GBrain index.
.venv/bin/python scripts/ingest_brain.py --input /external/path/brain-import.jsonl --format podcast --collection podcasts --apply --sync-index
```

`--brain-dir /external/path/brain` supports isolated installations/tests. The default comes from `BRAIN_DIR`, then `AFTERHOURS_HOME`, then the platform application data directory. The derived GBrain indexes default to `brain_directory.parent/gbrain/{private,public}`. Initialize GBrain with `scripts/setup_gbrain.py` first. `--sync-index` requires `--apply` and never broadens the requested scope. Restart long-running app/worker processes after installing new ingestion/retrieval code before starting imports.

The generic `--format reference` input is:

```json
{"source_id":"publisher:stable-source-id","source_kind":"document","title":"Example source","source_url":"https://example.org/source","text":"Source text","provenance":{"author":"Example author","published":"2026-01-01"}}
```

Optional `content_sha256` is the SHA-256 of imported text after CRLF normalization, BOM removal and outer whitespace trimming; a mismatch rejects the record. Source URLs must be HTTP(S) without embedded credentials. Source IDs are caller-owned, stable namespaced identifiers, not filesystem paths. Collection names are lowercase slugs. Changing the source ID creates a different record. A source ID is unique within its collection and scope; conflicting duplicates in one input batch are rejected. Identical repeated records coalesce.

The `podcast` adapter accepts the collector's `brain-import.jsonl` directly: `id`, `show`, `title`, `text`, `episode_url` (or `transcript_source`), plus optional `published`, `feed_url`, `guid`, `language`, `provenance` and `transcription_*` metadata. It builds `source_id = podcast:{show}:{id}`. It preserves the collector's `sha256` as **provenance.raw_sha256**, since that hash may cover the original file before front matter/captions were stripped. It computes its own `content_sha256` from imported text. `brain-chunks.jsonl` and the missing-transcript queue are not importer inputs. Producers should atomically replace their JSONL export to prevent a reader observing a partially rewritten file.

Canonical records live at `{brain}/{scope}/references/{collection}/{sha256(source_id)}.json`, with `schema_version`, `knowledge_role=external_reference`, `owner_endorsement=not_established`, full provenance/text, content and record hashes, revision, timestamps and active state. They are portable UTF-8 JSON, not the GBrain database. Files use mode 0600 and reference directories 0700. A scope/collection lock serializes writers. Unchanged imports do not rewrite files or increment revisions; metadata-only changes do increment revisions. Each record is replaced atomically. The complete input is validated before writes, but a batch is **not** a multi-file transaction: an I/O or indexing failure can leave applied records. Rerun safely; successful source writes are not rolled back because indexing failed.

Retrieval derives overlapping 5,000-character chunks from the entire source, including content beyond the first 60,000 characters. Every chunk carries its title, source ID/URL, content hash, bounded provenance, external-reference classification and lack of established owner endorsement. Both FileBrain and GBrain consume these chunks; GBrain updates changed hashes and removes obsolete chunks. The original complete text/provenance stays in the canonical record. Chunk offsets follow normalized text and are not audio timestamps.

Publication requires both `--scope public` and `--allow-public`; a private import is never copied to public automatically. References do not alter owner profiles, voice, wiki notes, bookshelf curation or beliefs. Importing a podcast is not evidence that the owner listened to or agreed with it. Adding a private source does not expose it to public retrieval.

Missing sources in an incremental input are left alone. Explicitly withdraw a source from retrieval with:

```sh
.venv/bin/python scripts/ingest_brain.py --collection podcasts --withdraw-source-id publisher:stable-source-id --apply --sync-index
```

Use the actual source ID (`podcast:show:id` for the podcast adapter). Withdrawal retains its canonical record/text with `active=false` for audit; it is not destructive deletion. Reimporting that source reactivates it. Previously generated conversation answers are not erased by withdrawal.

Success emits one JSON summary with `received`, `created`, `updated`, `unchanged`, `applied`, scope/collection and indexing status. Exit 1 means validation, I/O or indexing did not complete. Error output suppresses source contents, paths and provider diagnostics. Source records are limited to 2 MB of text, provenance to 32 KB, and input batches to 128 MB. Large corpora can be applied in smaller batches. Initial indexing costs scale with the number of chunks; subsequent imports sync only changed records.
