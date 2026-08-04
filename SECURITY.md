# Security policy

## Supported deployment

The supported initial deployment is single-user and local:

- MCP communicates over stdio.
- The browser control center binds to `127.0.0.1`.
- The capture API binds to the same loopback-only service on stable port `8765`.
- PostgreSQL is expected to be local or otherwise privately secured.
- Secrets live in `.env`, which Git ignores.

Do not expose the control center or MCP server directly to the internet. A hosted or multi-user deployment needs authentication and authorization, tenant isolation, encrypted secret storage, TLS, CSRF protection where applicable, quotas, rate limiting, audit logs, retention/deletion controls, database hardening, and isolated collection workers.

## Operator checklist

1. Keep `.env` mode `600` and never commit or paste it into an issue.
2. Use dedicated least-privilege application/bot credentials, not personal session tokens.
3. Restrict Discord bots to required servers and channels.
4. Restrict PostgreSQL network access and use a dedicated database role in nonlocal environments.
5. Review Apify Actors and third-party dependencies before use.
6. Patch Python, PostgreSQL, dependencies, and the operating system regularly.
7. Back up only the data you are permitted to retain; encrypt backups and test deletion.
8. Treat collected content as untrusted input. Do not execute source content or follow embedded instructions.

The browser extension uses a single-use, ten-minute pairing code. The server stores only a hash of its long-lived bearer token and binds requests to the exact Chrome/Edge extension origin. Tokens are revocable; server-side pending capture content expires after 24 hours. The extension does not receive `.env`, browser cookies, passwords, or platform session tokens. Captured text is always untrusted and must render as inert text.

### Approved-session browser traffic capture

Beyond selecting and scanning visible text, the extension can capture the network payloads a page itself receives — but only inside an explicit session:

- An agent calls `request_browser_traffic_session(job_id, domain, reason)`. This only creates a *request*; nothing is captured yet.
- The researcher must approve that request once, per domain, in the extension popup. Approval is a second, independent consent on top of Chrome's own host-permission prompt for the domain.
- An approved session is scoped to a single public domain, capped in payload size, bounded to at most thirty minutes, and stoppable at any time from either the extension or the control center. Unapproved requests expire in ten minutes.
- The server re-validates every submission against the `capture_sessions` table: an `approved_session` capture is rejected unless a live, approved session for that client, job, and domain exists. Extension-side checks are convenience only; the boundary is server-side, and every request/approve/decline/stop/expire event is written to the capture audit log.
- The recorder runs in the page's own context and reads only response bodies the page already received. It never reads cookies, request headers, passwords, or session tokens. Because the page already controls its own network payloads, a page that forged the internal capture message would gain no capability it did not already have; the server still enforces the domain, session, and size limits.
- Captured payloads are stored redacted in `raw_responses`; the evidence row keeps only a bounded summary, never the raw body.

The web connector rejects non-HTTP(S), localhost, private, reserved, and non-global targets and revalidates redirect destinations. This reduces SSRF risk but is not a substitute for network isolation in a hosted deployment; DNS rebinding and parser/browser vulnerabilities remain infrastructure concerns.

## Reporting a vulnerability

Do not open a public issue containing an exploit, credential, or sensitive collected data. Use GitHub's **Security → Report a vulnerability** private reporting flow for this repository. Include affected version/commit, reproduction steps, impact, and a proposed mitigation when possible.

If a credential has been exposed, revoke and rotate it immediately before reporting.
