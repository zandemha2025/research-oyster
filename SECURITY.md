# Security policy

## Supported deployment

The supported initial deployment is single-user and local:

- MCP communicates over stdio.
- The browser control center binds to `127.0.0.1`.
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

The web connector rejects non-HTTP(S), localhost, private, reserved, and non-global targets and revalidates redirect destinations. This reduces SSRF risk but is not a substitute for network isolation in a hosted deployment; DNS rebinding and parser/browser vulnerabilities remain infrastructure concerns.

## Reporting a vulnerability

Do not open a public issue containing an exploit, credential, or sensitive collected data. Use GitHub's **Security → Report a vulnerability** private reporting flow for this repository. Include affected version/commit, reproduction steps, impact, and a proposed mitigation when possible.

If a credential has been exposed, revoke and rotate it immediately before reporting.
