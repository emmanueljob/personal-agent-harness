# Application source and owner data

A clean installation has no career facts, recipient profiles, contact address,
campaigns, project links, or deployment hostname. Do not add these to source,
tests, plugin manifests, or build configuration. Tests use synthetic records.

Owner data is loaded at runtime:

- `BRAIN_DIR/public/`: reviewed profile, portfolio, voice, published notes and library.
- `BRAIN_DIR/private/` and `raw/`: private knowledge and original imports.
- `DATA_DIR/site-config.sqlite`: owner-edited presentation, campaign profiles,
  contact settings, analytics IDs and guide guidance; use `/admin` to edit.
- Other `DATA_DIR` databases: conversations, learning records, outreach and analytics.
- Environment or a secret manager: authentication, provider credentials, origin
  and deployment settings. Never copy local `.env` into an image.

Defaults keep these directories outside the checkout. Keep explicit overrides
outside it too. Public and private data remain separate; publishing a public
profile does not authorize publishing private notes or conversations.

## Importing an existing installation

A detached JSON bundle has `version: 1`, `settings` (the Settings schema),
`portfolio` (the Portfolio schema), and `profiles` (a list of Profile records).
Project links are optional HTTPS `url` fields on projects; they are never inferred
from project IDs. Keep the bundle outside the checkout with owner-only permissions.

```sh
python scripts/import_site_data.py /absolute/private/path/site-data.json
DATA_DIR=/absolute/private/path/state python scripts/import_site_data.py /absolute/private/path/site-data.json --apply
```

The default validates without writes. Import validates the entire bundle before
writing and inserts missing records in one transaction. It preserves existing
records and revisions; use the owner portal to reconcile existing edits.
It does not publish knowledge, migrate workflows, modify brain files, or delete
old data. Review the exact data before importing to a hosted public installation.
Back up the external configuration and brain independently of source control.

`fly.toml` and the plugin MCP URL are templates. Copy the Fly template outside
Git and supply the installation's app name, origin and redirects there; deploy
using that private configuration. Configure the MCP URL for the installation.
No deployment is part of this source change.

## Git history

Removing a value from the current source does not remove previous commits,
Git author identities, released images, logs, forks or caches. A history cleanup
requires a separately coordinated rewrite and cleanup of published artifacts.
Rotate an exposed credential rather than relying on deleting its history.
