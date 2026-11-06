# Short outreach links

The admin portal generates `https://<site>/hi/<name>` links. The page is served at
that URL; it does not redirect to a long UTM query string. The same public profile
selection and private visit attribution still apply.

In **Admin portal → Outreach links**, enter the person/company/source and optionally
choose **Public URL name**. The automatic suggestion uses the first word of the
person label (or company if no person was supplied), normalized to ASCII lowercase.
A duplicate automatic name gets a suffix such as `alex-2`. A custom URL name must
be unused and contain up to 60 lowercase letters, digits or hyphens. The recipient
name in the path is public. Company/source labels remain in the private database.
A short URL is a public presentation choice, not an authentication mechanism.

Existing UTM links continue to work. For an older registered link, **Shorten & copy**
creates an alias for the same attribution codes and copies the clean URL. It does
not reset counts or overwrite another person's link. Future copy operations reuse
that alias. Revoking/reactivating a link affects both formats; disabled tags also
make a short alias unavailable. Unknown/revoked aliases return a neutral 404.
Previously saved browser profile context can persist, as for legacy links.

Short aliases live in the additive `outreach_short_links` table in the existing
private outreach SQLite database. The mapping is not shipped with source or build
images. Registration is serialized and collision errors roll back tags and links.
GET requests do not create aliases. Short pages use no-store and noindex headers;
these headers do not make a known URL private or prevent someone sharing it.

The page embeds the resolved public profile without exposing attribution codes.
The visitor script sends the public greeting slug to the first-party event relay,
which resolves attribution on the server. It normalizes the page URL and drops
referrers before configured analytics delivery. The PostHog integration continues
to receive only its existing content-free event properties. No new pixels or
tracking providers are enabled. Optional third-party pixel scripts have their own
behavior and can observe the browser page URL when configured.

The greeting persists in sessionStorage for ordinary navigation during the visit.
An explicit legacy UTM arrival replaces this saved greeting context. The `/hi/`
path takes precedence over query tags and an older saved profile.

API compatibility: existing POST `/api/admin/links` and `/api/outreach-links`
clients without new fields retain legacy output. Send `short: true` or a nonempty
`slug` to request a short link. POST `/api/admin/links/{id}/shorten` is owner-only.
The CLI now generates short links by default and supports `--slug`; register with
`--server` for live links, because a locally registered mapping is local only.

No Temporal execution or model changes are involved. Preserve the persistent
volume; losing the outreach database loses the mappings for both URL formats.
