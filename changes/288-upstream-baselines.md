---
issue: 288
type: changed
---
Raised the verified Graphify baseline to 0.9.87 after re-verifying extraction, update, watch, Obsidian export, git hooks, `install --platform hermes`, the `graphify-mcp` stdio server, and default ignore-file honoring (including every pattern in the bundled `.graphifyignore` template) against a real `graphifyy[mcp]==0.9.87` install side by side with 0.9.71 (no change to the surface OMES uses), and raised the documented-but-not-live-tested Coolify adapter baseline to v4.4.6 after reviewing the v4.4.0-v4.4.6 release notes and the API route/controller source for the endpoints OMES's Coolify client actually calls (unchanged); the review scope and its limits are recorded in `docs/coolify-adapter.md`.
