# Research Oyster Capture extension

This Chrome/Edge Manifest V3 extension creates a supervised bridge between pages a researcher is already allowed to view and an active Research Oyster brief. It never joins communities, reads cookies or passwords, bypasses permissions, or silently uploads page content.

## Install from source

1. Start Research Oyster and open its capture/onboarding screen to create a one-time pairing code.
2. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
3. Enable **Developer mode**, choose **Load unpacked**, and select this `browser_extension` folder.
4. Open the extension, choose **Pairing & settings**, enter the code, and keep the local address at `http://127.0.0.1:8765` unless Oyster shows another local port.
5. Keep **Anonymize captured authors by default** enabled unless a research protocol specifically requires attribution.

## Use it

1. Choose the active Oyster research job. The job's mission and search terms appear in the popup.
2. Either select text and use **Review in Research Oyster** from the context menu, or click **Find visible candidates** to inspect passages currently visible in the page viewport.
3. Review each candidate. **Approve & save** is the only action that sends evidence to Oyster. Reject and Clear discard local candidates.
4. Use **Stop** to prevent new captures immediately. Stopping does not discard the review queue.

The adapters recognize visible content on Discord, X, Twitch, Kick, Reddit, and generic webpages. Page-reading code is injected only after the user clicks **Find visible candidates**; it is not a persistent all-sites content script. Website layout changes can prevent an adapter from finding content; selected-text capture remains the reliable fallback.

## Expected local API

The service must listen only on loopback. The extension rejects remote and HTTPS configuration values by design because the initial local service contract is plain HTTP.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/capture/pair` | Exchange `{code, client_name}` for `{token}`. Code must be short-lived and single-use. |
| `GET` | `/api/capture/jobs` | Return `{jobs: [{id, title}]}` or an array of jobs. |
| `GET` | `/api/capture/jobs/{id}/mission` | Return the original `brief`, `research_questions`, and `look_for` terms. |
| `POST` | `/api/capture/approve` | Atomically persist an approved capture. Requires `approved_by_user: true` and an idempotent `client_capture_id`. |

All authenticated endpoints use `Authorization: Bearer <pairing token>`. The API should enforce Origin allowlisting for the installed extension ID, constant-time token comparison, request/body limits, capture rate limits, job authorization, token revocation, and audit events. It must not return platform credentials to the browser.

Capture payloads contain the immutable originating `job_id` and `job_title`, `research_question`, `source_type`, `url`, `page_title`, editable `excerpt`, `researcher_note`, `captured_at`, `capture_mode`, `anonymized`, `client_capture_id`, and `approved_by_user`. The original text remains only in the local review queue to support the per-item anonymization toggle; `raw_excerpt` is removed from the approval payload whenever anonymization is enabled. The extension refuses cross-job approval; the server requires explicit approval, validates the active job and its question, and uses `client_capture_id` as an idempotency key.

## Tests

No build or package installation is required. With Node 18 or newer:

```sh
node --test browser_extension/tests/core.test.js
```

## Caveats and responsible use

- Being able to view something does not automatically grant permission to collect, retain, analyze, or republish it. Follow community rules, client policies, platform terms, privacy law, and the agreed research protocol.
- Anonymization is best-effort. Names can appear inside prose, images, URLs, or quoted replies. A person must review every capture.
- Visible-page scanning is intentionally bounded to 25 candidates and the current viewport. It is not a bulk scraper or unattended monitor.
- Dynamic sites change frequently. Verify quotes against their source and preserve only the minimum context needed.
- Captures can contain personal, copyrighted, sensitive, inaccurate, or deleted content. Apply retention limits and delete material that is not necessary.
