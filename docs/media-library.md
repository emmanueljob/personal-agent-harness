# Books, podcasts and notes

The external brain remains canonical. `public/learning.json` holds reviewed owner
notes; `public/bookshelf.json` holds the selected shelf; active records in
`public/references/podcasts` hold episode metadata and reference transcripts.
The media catalog joins these sources at read time. Nothing is copied into the
source repository or promoted from private to public by a search or page view.

The Podcasts tab is a listening room with show filters, search, a notes-only
filter, episode details, personal reactions, listening status and original links.
Episode cards use original episode cover images inside the existing CSS depth treatment.
When episode-specific artwork is unavailable, the show image is used; missing or
failed images fall back to the waveform design. The selected episode displays its
cover in the detail panel too. They do not play
or download audio. Deep links use `/?episode=<published-id>#podcasts`.
The bookshelf now displays the current published reading note and searches
comments/tags as well as title/author, without rewriting canonical shelf metadata.

`search_library` is available to the chat agent, public HTTP, remote MCP and
browser WebMCP. It searches title, author/show, tags and notes across media kinds.
An empty query lists annotated sources first; `notes_only=true` limits results to
recorded owner reactions. Generic recommendation words do not accidentally filter
out every source. Public searches never read private notes or reference files.
The private assistant can search both scopes, with active private notes taking
precedence in its own results.

- `GET /api/library?kind=podcast&notes_only=true`: UI catalog, with show filters.
- `GET /api/library/podcast/<id>`: one currently published episode for a deep link.
- `GET /api/public/library`: public agent API, with `query`, `kind` (`all`, `book`,
  `podcast`), `notes_only`, `offset`, `limit` and `next_offset`.
- Agent tool `search_library`: small pages of full notes with source URLs and
  `next_offset`; calls are recorded in the existing tool-results log.
- Use `search_knowledge` for transcript excerpts or broader principles; the media
  catalog does not return full transcripts or confuse them with owner beliefs.

The recommendation guidance permits useful suggestions grounded in a specific
owner reaction. It does not require a separately maintained favorites playlist.
Negative/mixed notes remain intact; only explicit statements establish favorites
or blanket endorsements. Reading/listening status is separately recorded.
Reference-only picks must be described as the agent's suggestions.

New media recommendation turns receive a bounded catalog of published notes in
their initial knowledge context, inside the existing context budget. Existing
saved execution snapshots stay unchanged on retries. Workflow definitions and
activity names are unchanged; the extra tool is available to newly prepared
turns. Both real Temporal recovery tests should run before release.

Episode metadata is cached by canonical file name, size and modification time.
Notes are read on every catalog request so edits and unpublishing take effect
immediately. Removing a reference invalidates the derived metadata. An explicitly
published note remains available even if its original reference is absent, with
`reference_available=false`; private material is never opened to enrich it.
No new database schema, dependencies or knowledge publication are required.


## Episode artwork

Artwork URLs are separate, scoped metadata in `public/podcast-artwork.json`, outside
Git and the image build. The public-brain exporter includes this file. The catalog
reads only the same scope's metadata and only decorates currently visible episodes.
Provided artwork fields in newly ingested podcast metadata are retained too.

Refresh existing published episodes from their original feeds after imports:

```sh
python scripts/sync_podcast_artwork.py --brain-root /path/to/external/brain
```

The sync matches exact episode GUID/link identities, then normalized titles, and
falls back to show artwork. Each feed is fetched once with bounded size, redirects,
timeouts and checked/pinned public IPs. It never downloads audio, calls a model,
changes listening status or rewrites transcripts. The manifest records whether the
image was episode or show artwork and the original feed URL. Prior artwork survives
a failed feed refresh, while withdrawn episode IDs are removed from the manifest.
Image loads are lazy and send no referrer. Artwork JSON and images stay out of Git.
