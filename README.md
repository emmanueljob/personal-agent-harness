# Personal agent harness

Local career and creative assistant with separate public/private modes. Deep Agents runs the LangChain/LangGraph model/tool loop inside Temporal workflows. Model calls and application tools run as individual Activities. SQLite holds profiles, turns, retry transcripts and artifacts outside the checkout.

## Start locally

Prerequisites: Python 3.12+, Node.js, Ollama and Temporal CLI. Install Python dependencies with `uv sync`, and frontend dependencies with `npm --prefix frontend ci`. Copy `.env.example` to `.env` and keep credentials local.

This workspace’s local launcher uses tools installed under `.runtime/tools/`:

```sh
.venv/bin/python scripts/dev.py
```

Open http://localhost:8000 for chat and http://localhost:8233 for Temporal workflows. Ctrl-C stops the services started by the script. Existing services are reused. On macOS, runtime state and logs are under `~/Library/Application Support/Afterhours/state/`, outside the checkout. To use private mode, enter OWNER_TOKEN from the local .env file in the UI. Do not share that file or paste its contents in chat.

The example configuration selects local Ollama inference and Temporal execution. All inference uses local Ollama; no OpenAI API requests or automatic cloud fallback. Ollama cloud is disabled by the launcher. Models live in .runtime/models/. Context is configured at 16,384 tokens to preserve memory headroom; the model's advertised maximum is not a memory budget.

To download another model using the running local server:

```sh
.runtime/tools/ollama/ollama pull qwen3.6:35b
```

Set OLLAMA_MODEL to the desired installed model in .env and restart the web/worker processes. Existing turns retain their original model/profile/history snapshot on retries; new turns use the new configuration. For OpenAI, set MODEL_PROVIDER=openai and configure OPENAI_API_KEY. Keep .env private; it is excluded from version control.

## Durable turns

1. The API commits a queued turn in SQLite before acknowledging it. Clients can supply request_id for idempotent submissions within a thread. Reusing it with different text returns 409.
2. The dispatcher submits one turn at a time with a deterministic Temporal workflow ID. If it crashes after submitting but before observing success, it attaches to that workflow after restart. Completed workflow IDs cannot accidentally be reused.
3. New turns select `AGENT_RUNTIME=deepagents` at enqueue time. Their versioned workflow uses the official `temporalio-deepagents` integration: every model and application tool call is an Activity with bounded retries. Temporal replays completed Activity results after worker recovery.
4. Profile, voice, retrieved context, conversation input and model configuration are snapshotted per turn. Before model/tool calls, the workflow also saves a transcript through an Activity for explicit retries after a workflow exhausts retries. Completed turns short-circuit execution.
5. Artifact writes are upserts keyed by turn/title; web evidence is cached per turn/arguments. Explicit retry creates a new workflow generation and resumes its saved transcript. A pending tool call can repeat on explicit retry, so external writes require idempotency.

This is **at-least-once execution**, not exactly-once model calls. A crash between a model response and its checkpoint can repeat inference. Future plugins that write externally must provide their own idempotency keys. One shared local data directory and one managed worker are the supported deployment; this is not a distributed SQLite service. Legacy turns retain their existing SQLite graph checkpoints and whole-turn Activity/OS lock. That lock is macOS/Unix-specific. New Deep Agents turns use Temporal history rather than a LangGraph SQLite checkpointer.

## Routines

Use Owner → Routines in a private conversation to create interval routines, run immediately, list and disable them. The desired configuration is committed in SQLite and reconciled to Temporal Schedules, including after restarts. Disabled routines are checked again by the activity, so a delayed trigger cannot enqueue disabled work. Temporal catches up triggers within a five-minute window and skips overlapping schedule workflows. Each scheduled workflow has a deterministic queue ID for activity retry deduplication. Schedules produce queued turns; the dispatcher executes them serially.

Temporal keeps timers while the web app is down. A worker is required to materialize scheduled queue entries and execute turns. After Mac sleep or shutdown, work resumes when the services return; the laptop does not run while off. This is a persistent **development server**, not a production Temporal deployment. Keep both `state/harness.sqlite`, `state/temporal.sqlite` and `state/checkpoints.sqlite` in the application data folder when backing up or restarting. Do not reset just one database while preserving the others.

## Tools and profiles

Public runs load only the published profile and public voice guidance, and can search public knowledge. Private runs load a separate private profile and can search both scopes and save Markdown artifacts. Biography and presentation data are loaded at runtime. Publishing profile changes applies to future turns; existing conversations retain their history/snapshots.

Register trusted tool factories in harness/agent.py. They receive mode, thread ID, turn ID and storage. MCP is a future adapter: connection lifecycle, credentials, tool allowlists, approvals and external-write idempotency still need implementation.

Public thread UUIDs are capability links; anyone with one can read that conversation. OWNER_TOKEN protects private routes and profile editing. All services bind to loopback. Before internet hosting, add real identity, visitor session isolation, rate/spend limits, plugin policy and backups. Hosting direction: web and worker containers, Postgres/checkpoints, object storage and a managed or self-hosted production Temporal service.

## Validation

```sh
.venv/bin/python -m pytest -q
TEMPORAL_TEST_ADDRESS=localhost:7233 .venv/bin/python -m pytest -q
```

The Temporal integration test kills a worker mid-activity, starts a replacement, verifies recovery and one artifact row, then creates/triggers/disables a real schedule. The Deep Agents recovery test kills a worker after an artifact tool completes, recovers the interrupted model Activity and verifies the tool Activity ran once. Test workers and task queues are isolated from the app. It uses a fake agent so it makes no paid or local model calls.

## Interface development

The frontend is React + TypeScript + Vite, with Motion transitions and locally bundled DM Sans and DM Serif Display fonts. The portfolio uses separate tabs for conversation, work, background, bookshelf and the technical walkthrough. Dark charcoal/amber and warm light themes are available; the theme preference persists locally. Personal presentation fields come from `/api/portfolio`, not the frontend bundle.

FastAPI serves frontend/dist at the existing localhost:8000 URL after a frontend build:

```sh
PATH="$PWD/.runtime/tools/node/bin:$PATH" npm --prefix frontend run build
```

Restart the local stack after the first build (FastAPI registers the asset directory on startup). Further frontend builds update the served files; refresh the browser. For hot reloading, run `npm --prefix frontend run dev` with the same PATH setting; Vite proxies /api to FastAPI.

The working animation reflects queued/running state and persisted LangChain model/tool callbacks, not private reasoning. No invented research/review stages or token stream are displayed. Public API event results are scoped to the authorized thread. Desktop/mobile layouts, native modal focus handling and reduced-motion preferences are supported. The owner's token is held only in browser memory and sent only to the local API.

## Personal knowledge outside the repository

The default application home is `~/Library/Application Support/Afterhours` on macOS and `$XDG_DATA_HOME/afterhours` (or `~/.local/share/afterhours`) elsewhere. Override with `AFTERHOURS_HOME`, or independently set `DATA_DIR` and `BRAIN_DIR`. These are runtime locations, not publishing directories.

The brain contains `public/profile.md`, `public/portfolio.json`, `public/voice.md`, and `public/wiki/` notes; private equivalents live under `private/`. Original source material belongs under `raw/`, which retrieval does not search. Public presentation uses an allowlisted schema. Owner studio → Brain adds public or private Markdown notes; notes default to private. Profile editing requires owner authentication. Never commit personal knowledge, exports, credentials, or runtime databases.

Voice guidance is a style summary, not raw email storage. Public guidance contains abstract writing preferences; private sampling metadata and analysis remain private. Recent writing sets the professional baseline, with older writing informing warmth and restrained humor. Guidance never overrides evidence or AI identity.

Retrieval supports the original file provider and a local [GBrain](https://github.com/garrytan/gbrain) provider. Markdown and bookshelf JSON stay canonical; the GBrain databases are derived indexes. The old shared work-brain kit is not installed or activated.

### Runtime bookshelf

`GET /api/bookshelf` returns only the explicitly curated public snapshot. Private imports live at `brain/private/bookshelf.json`, raw browser metadata at `brain/raw/kindle/`, and the public selection at `brain/public/bookshelf.json`, all beneath the existing external application home. No inventory belongs in source control or frontend bundles.

In **Owner studio → bookshelf**, enter owner access, load the private inventory, select entries, optionally edit category/status/comment/attribution, and preview the 3D shelf. **Review … books for visitors** lists the exact metadata and comments to be exposed. **Apply reviewed selection to local site** replaces the public snapshot; it does not deploy. Private draft edits do not change public data until applied. A changed inventory invalidates stale reviews. Reimports merge by ID and preserve owner edits, with new entries private by default.

Metadata JSON import accepts `{ "source": "Kindle browser library", "complete": false, "books": [...] }`. Each book needs an `id` and `title`; optional fields include `author`, `cover` (HTTPS Amazon media URL), `sample`, `status` (default `unknown`), `category` (`work`, `personal`, or `unclassified`), `comment`, `comment_source`, and `recommendation` (default `unknown`). Import metadata only, never full book text or credentials. Browser sign-in is separate from Google sign-in and remains manual if Amazon requests authentication. Work categories are proposed curation aids, not inferred reading or endorsement.

Owner API routes require the same bearer token as the existing studio: `GET /api/owner/bookshelf`, `POST /api/owner/bookshelf/import`, `PUT /api/owner/bookshelf/{id}`, and `POST /api/owner/bookshelf/publish` with `{ "ids": [...], "revision": "..." }`. Public agent retrieval reads only the public snapshot; owner retrieval can read the private inventory. Imported text is untrusted data, never instructions.

Spines use CSS perspective and transforms with native buttons; Enter/Space opens a cover dialog, Escape returns to the spine. Small screens scroll individual shelf rows horizontally. System reduced motion disables book transforms and transitions. Both Afterhours themes share the existing design tokens.

For frequent additions, **Add a book +** accepts a title and author, optional commentary, category, and cover URL. New manual entries are labeled owner-added metadata rather than Kindle inventory. Check or uncheck books, then review and apply the selection to add or remove them from the visitor shelf without deleting the private inventory.

The reading interaction has two stages: selecting a spine animates the book from its shelf position into a cover view; **Open the book** hinges the jacket onto a paper spread containing the attributed comment and metadata. **Close the book** returns to the cover; Escape or **Return to shelf** restores focus to the selected spine. Actual cover artwork appears on the jacket and side faces; reconstructed spine typography is not a scan of the publisher’s spine. Recalled titles without a cover use a typographic jacket. Phone screens put both notes and details on one scrollable page. Reduced motion presents the same states without the pull-out animation or animated hinge.

Optional runtime `spine` references store a source photo URL, source page, original dimensions, and four spine corners. The frontend projects those corners onto the book face using a homography, preserving the photographed lettering and publisher mark instead of generating artwork. These references and downloaded research photos stay outside source. A physical edition need not match the Kindle cover; this is a visual reference, never proof of owning that physical copy. Source links appear inside the opened book. Unmatched books retain reconstructed spines.

Book inspection now uses a lazy-loaded Three.js WebGL 2 scene. Actual geometry forms the jacket, spine, page block, hinged cover, and flexible opening leaf. A single full-viewport scene persists across hover, pickup, inspection, and return. Hover reveals the cover and depth beside the source slot; critically damped pose interpolation carries that same volume into inspection. The source bounds are read live so scrolling and resizing keep the return anchored. A projected hit area follows the entire preview, so the cover and edges are clickable. Nearby jackets and the renderer are warmed ahead of hover. Drag (or arrow keys) turns the object through 360 degrees; Home resets it, and +/− zoom. Clicking anywhere on the closed book opens it. Notes are drawn onto the inside pages from runtime data, with a readable HTML edition through **Read notes** (shown automatically on phones). WebGL failure falls back to the cover and HTML notes. Reduced motion switches between settled poses without pickup, hinge, or return animation. GPU resources and animation loops are released when the preview or inspector closes.

The visitor shelf now offers **Spines / Covers** views with the same filtering, search, and inspection behavior. View preference is browser-local. Cached spine and cover images live beneath the external public brain folder and are served only while their book is in the current public selection. Covers for WebGL are fetched only from validated Amazon media URLs, capped at 5 MB, with redirects disabled.

## Local GBrain provider

Run `.venv/bin/python scripts/setup_gbrain.py`, set `BRAIN_BACKEND=gbrain` in `.env`, and restart the stack. Setup downloads the pinned official GBrain v0.60.530.0 release for Apple Silicon macOS or x64 Linux when needed; `GBRAIN_BINARY` supports a separately installed executable. `BRAIN_BACKEND=files` restores the original provider without moving or rewriting personal content.

The adapter uses the local CLI with a minimal environment that excludes model/API credentials and inherited GBrain routing. It creates separate public/private PGLite databases under the brain folder's sibling `gbrain/` directory. It does not register a global Codex plugin, enable automatic capture, install background jobs, expose an MCP port, or configure paid providers. Conservative keyword search is enabled; semantic embeddings, reranking, extraction and enrichment remain off.

Each search synchronizes changed Markdown and individual bookshelf records, then searches only the appropriate database(s). Public queries never open the private index. File locks serialize PGLite access across the web/worker processes. If a same-scope index is busy, retrieval uses a twice-validated canonical-file search in that scope instead of waiting for the bulk build; returned hits explicitly report `backend=files` and `fallback_reason=index_busy`. Removed records are soft-deleted from the derived index. A live source/hash allowlist rejects unmanaged, changed or withdrawn hits, including changes during a query. A failed sync/search fails closed with a generic error; it does not broaden visibility or silently switch providers. The owner panel displays the configured provider.

The first index build can take several minutes for a larger library; unchanged records are not rewritten on later queries. Raw imports, style guides and sampling manifests are excluded from indexing. Back up the canonical knowledge files and runtime databases; these indexes can be rebuilt from the source files. Soft deletion is not physical erasure of database history or backups. As with the rest of this local app, filesystem access belongs to the trusted owner/server; this is not OS sandboxing between processes.

Run the opt-in binary integration check with `GBRAIN_TEST_BINARY="$PWD/.runtime/tools/gbrain/gbrain" .venv/bin/python -m pytest tests/test_gbrain.py -q`. It uses isolated temporary databases and verifies real indexing, visibility, updates and withdrawal without paid model calls.

## Public-web research tools

`search_web` and `read_webpage` are registered alongside knowledge search and private artifact generation. `WEB_SEARCH_PROVIDER=ddgs` (default) uses DDGS with its DuckDuckGo backend and needs no API key. Search queries reach the search service; page reads reach the public source. Inference remains with the configured model. Keyless search can be rate-limited or unavailable and has no service guarantee. Set `WEB_SEARCH_PROVIDER=disabled` to remove both tools, or explicitly select `tavily` and configure `TAVILY_API_KEY` for Tavily basic search. Credentials remain server-side.

The reader uses verified public IP addresses, preserves the original hostname for TLS validation, rechecks redirects, and excludes local/internal destinations, nonstandard ports, browser credentials, JavaScript and binary downloads. Page text and snippets are untrusted evidence; the model is instructed to ignore page instructions and cite original URLs. Those instructions are not a guarantee against every model error. Personal claims still come from the approved scoped brain.

Successful research results are cached per turn/tool/arguments in the external SQLite state. Retries reuse the same evidence and retrieval timestamp. Each turn is limited to four successful web calls and a bounded output budget; provider errors return explicit errors rather than fabricated results. Context budgeting leaves room for web evidence. Search/read tools have explicit activity labels. Completed answers retain an expandable tool trail and real source links; `/api/tools` lists the visitor tool catalog. Private traces require owner authorization and are never returned in public threads.

Model middleware stops offering tools after four tool results and asks the model to answer from gathered evidence. Tool evidence passed to each model call is capped at 12,000 characters in total, preserving the stored full results and checkpoint identities. Knowledge-tool text is also bounded. This prevents repeated retrieval from filling the local model context or keeping the visitor waiting through an unbounded research loop.

## Behind-the-curtain walkthrough and bookshelf actions

The public **The machinery** navigation opens a Three.js architecture explorer. It illustrates a normal turn, worker recovery and bookshelf tool use with selectable components, orbit controls, slide steps and opt-in playback. These are explanatory scenarios, not live telemetry or failure injection. A flat diagram is available and is the default on small screens. The scene loads lazily near the viewport and releases its renderer when offscreen.

`browse_bookshelf` searches only the published inventory and returns up to eight real book IDs with bounded attributed notes. Its structured selection is persisted with the turn. The thread endpoint revalidates selected IDs against the current public shelf, including historical turns. React displays a result card and temporarily filters the existing bookshelf; the visitor can jump to it, inspect the existing 3D books, or return to all books. No library metadata, curation or publication changes are performed by this tool. Private inventory is never searched by this visitor-facing tool, including in owner mode.

Portfolio navigation uses five accessible tabs: Conversation, The work, The person, The bookshelf and The machinery. Hash routes preserve direct links and browser back/forward navigation. Inactive panels are hidden while conversation drafts, shelf filters and architecture walkthrough state remain mounted. Cross-section question actions open Conversation; saved book-result links open The bookshelf. Leaving the shelf closes any active book inspector. Arrow keys, Home and End navigate the tab list.

## Git privacy guard

Run `python3 scripts/setup_git_safety.py` after cloning to activate the pre-commit and pre-push hooks. They inspect real Git objects, credentials and sensitive data paths. The pre-push scan includes history, not just the latest files. On the owner’s machine, local private-reference fingerprints add another check. See [the policy and limitations](security/README.md). Runtime data, résumés and credentials are excluded from this repository.

## Deep Agents comparison

An opt-in, isolated [Deep Agents + Temporal comparison](docs/deepagents-comparison.md) uses the same local model, frozen initial context, brain and application tools. It has its own dependency environment, queues, state and private reports. The serving application now defaults to Deep Agents when configured with the example environment. See [runtime migration and rollback](docs/deepagents-runtime.md).
