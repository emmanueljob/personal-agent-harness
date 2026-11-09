# Third-party software

The isolated worker depends on `mcp-server-linkedin` 4.29.0 by Daniel Sticker
(2025–2026), licensed under Apache 2.0. Its persistent BrowserManager is used
without automatic cookie import or the upstream server's send tools.
Source: https://github.com/stickerdaniel/linkedin-mcp-server
Reviewed revision: 48e08f1980b27a07d9e1d6cf093842a02ed7b735.
License: [Apache 2.0](licenses/linkedin-mcp-server.txt).

Read-side GraphQL and realtime protocol implementations were informed by and
adapted from `allman-cli`, Copyright (c) 2026 Tarka Ventures, Inc. (MIT).
Source: https://github.com/tarkaai/allman-cli
Reviewed revision: 664de1ec8b27f37d5e6702d7bc8e7c9650b5cf79.
License and upstream scope notice: [MIT](licenses/allman-cli.txt).

The worker implements its own private queue, owner gateway, and capture receiver.
It does not use upstream auto-commit/history storage, personal browser cookie
extraction, send-message endpoints, or mark-read operations.
