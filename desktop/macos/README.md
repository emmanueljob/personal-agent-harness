# Personal Brain: Mac access prototype

First milestone: a native SwiftUI window/menu-bar app that verifies Calendar inventory
and local Messages read access. No imports, model calls, network listener, Python
worker, background collection or changes to the existing harness are introduced.

Requires Apple Silicon, macOS 14+, Python 3 and the Apple Command Line Tools.

```sh
python3 desktop/macos/build.py --test --launch
```

The build stays in the worktree's ignored `.runtime/personal-brain-macos/` directory.
Open `Personal Brain.app`, then use each source's Check button. A first build can take
longer while Swift builds its module cache. Quit the app before rebuilding it.

## Permissions and verification

Calendar requests full access via EventKit because macOS does not offer a read-only
EventKit permission. This code only reads the number of visible calendars; it does
not query events or write changes. Denial/restriction is displayed with Settings access.

Messages opens `~/Library/Messages/chat.db` with SQLite read-only flags, then executes
`SELECT 1 FROM message LIMIT 1`. It verifies table readability without fetching text,
participants, identifiers or attachments. Missing, blocked, incompatible and failed
databases are reported without leaking SQLite diagnostics. This is an access probe,
not verification that a complete history can be parsed or imported.

If blocked, open System Settings → Privacy & Security → Full Disk Access and add the
actual app using + (Show app in Finder reveals its location). The owner must grant
this broad system permission. Quit/reopen, then check again. Never change TCC databases,
disable system protections or grant permissions to Terminal to make the probe pass.

Results are written locally to
`~/Library/Application Support/PersonalBrain/access-status.json`, with directory mode
0700 and file mode 0600. It contains states, generic explanations, a calendar count and
timestamp; no personal source content. It is overwritten by subsequent checks and
never uploaded. A blank state after relaunch is intentional: access must be checked
again rather than showing a stale success.

The prototype uses ad-hoc signing with a stable designated identifier because a
Developer ID is not required for a local development build. This is not notarized
distribution or a publisher-authenticated identity; do not rely on this development
identity to resist impersonation. Rebuilds or moving the app may require permission
reapproval. Developer ID signing/notarization is a later gate. No App Sandbox is enabled;
the app has the ordinary privileges of the owner process.

Window close leaves the menu-bar app running; Quit stops it. Tests only open synthetic
temporary databases. They cover empty/populated tables, read-only byte stability,
missing files, invalid SQLite files and unsupported schema. Real permissions must be
verified from the app launched by macOS, not a shell-launched probe.

Swift source is allowed for publication only under `desktop/macos/Sources/`; compiled
apps, SQLite databases and generated plist files remain excluded from source control.
