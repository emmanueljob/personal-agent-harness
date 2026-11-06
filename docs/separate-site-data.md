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

## Downloadable resume

The app serves only an explicitly published `BRAIN_DIR/public/resume.pdf` at
`/resume.pdf`, with a download link in the header and footer. `/api/resume` reports
availability; the public profile API also advertises it. A fresh installation has
no resume and shows no download link. Private files, symlinks, invalid PDFs and
files over 20 MiB are never served. Downloads use a fixed filename and no-store
headers; replacing the file does not require a code deploy.

Review the PDF itself, including contact details and links, before publication:

```sh
python scripts/publish_resume.py /absolute/private/path/reviewed-resume.pdf
python scripts/publish_resume.py /absolute/private/path/reviewed-resume.pdf --publish
```

Use `--brain-root` for an installation's external data folder. Updating an existing
resume requires `--replace`; take a backup first. Removing the public PDF withdraws
the link on a page reload. Public downloads may have been saved by visitors.
The optional public PDF is included in explicit public-brain exports and normal
data backups. Neither the PDF nor extracted resume text belongs in Git or the
container image. Publishing a download does not rewrite the agent's knowledge,
voice or approved career facts; those are reviewed separately as data.
