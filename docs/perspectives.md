# Digital twin perspectives

The public agent speaks in first person as a labeled AI digital twin. It uses scoped owner knowledge for personal facts and opinions, while podcast transcripts remain external references. Both legacy and Deep Agents execution use the same prompt builder and turn preparation.

Store owner-authored perspective data in `{brain}/{public|private}/perspectives.json`, outside Git. The public exporter includes only the public file. Source privacy checks reject this filename if someone attempts to commit it.

```json
{
  "version": 1,
  "entries": [{
    "id": "example-view",
    "topics": ["prototyping", "product development"],
    "basis": "owner_confirmed",
    "view": "I like testing a small prototype with users early.",
    "owner_source": {"type": "owner_statement", "recorded_at": "2026-11-03"},
    "influences": [{
      "source_id": "podcast:example:episode-id",
      "title": "Example episode",
      "url": "https://example.org/episode",
      "listening_status": "owner_reported",
      "relevance": "The owner explicitly linked this episode to the view."
    }],
    "limits": ["This is a personal preference, not a universal recommendation."]
  }]
}
```

`basis=owner_confirmed` means the owner explicitly supplied the view. `basis=inferred` requires the response to identify the interpretation as tentative. `listening_status=owner_reported` records the owner's own account of listening; `unknown` makes no listening claim. Neither imported transcripts nor an explicitly public source establish agreement or listening on their own. A specific owner perspective may link to an external source without endorsing every claim in that source.

Perspective lookup runs before general knowledge retrieval and is snapshotted with the turn. Knowledge-tool results prioritize matching perspective records so transcript hits cannot crowd out the owner's stated view. Public lookup reads only public records. A matching source link carries its original URL into the model context; ordinary sources continue to be retrieved from the scoped library. Evidence is bounded to 4,000 characters per record and 6,000 across at most three records. Initial perspective and general-knowledge evidence share a 10,000-character budget. Knowledge-tool responses include only complete perspective records that fit their budget, preserving intact citations.

The twin should lead with a useful view, explain its influences naturally, and cite original episode links. Confirmed opinions remain opinions rather than independent factual verification. For extrapolations from owner principles and source ideas, the twin should indicate uncertainty rather than inventing a firm personal position. It must distinguish AI safety from cybersecurity, avoid treating advertisements as guest claims, and remain honest about its AI identity.

New turns use updated perspectives and voice guidance. Existing turn snapshots retain their original evidence. Updating source code alone does not publish owner data: export and transfer the separately reviewed public knowledge bundle to update a hosted deployment.
