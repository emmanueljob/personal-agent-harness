# Third-party software

The previous worker used `mcp-server-linkedin` 4.29.0 by Daniel Sticker
(2025–2026), Apache 2.0. That runtime dependency has been removed in favor of a
native Chrome desktop. Its license remains here for the earlier published version.
Source: https://github.com/stickerdaniel/linkedin-mcp-server
License: [Apache 2.0](licenses/linkedin-mcp-server.txt).

The admin desktop viewer uses unmodified `@novnc/novnc` 1.7.0, Copyright (C)
2022 The noVNC authors, licensed under MPL 2.0. The covered source is available
from https://github.com/novnc/noVNC and the pinned npm package; no noVNC source
files are modified. See [MPL 2.0](licenses/novnc-mpl-2.0.txt),
[upstream notice](licenses/novnc-notice.txt), and its bundled
[pako MIT/zlib notices](licenses/pako.txt). Google Chrome is obtained from
Google's signed Linux repository; X11/Openbox/x11vnc packages come from Debian.

Read-side GraphQL and realtime protocol implementations were informed by and
adapted from `allman-cli`, Copyright (c) 2026 Tarka Ventures, Inc. (MIT).
Source: https://github.com/tarkaai/allman-cli
Reviewed revision: 664de1ec8b27f37d5e6702d7bc8e7c9650b5cf79.
License and upstream scope notice: [MIT](licenses/allman-cli.txt).

The worker implements its own private queue, owner gateway, and capture receiver.
It does not use upstream auto-commit/history storage, personal browser cookie
extraction, send-message endpoints, or mark-read operations.
