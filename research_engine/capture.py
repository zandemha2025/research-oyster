from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from db.queries import connect, json_value
from research_engine.raw_log import record_raw_parts


PAIRING_TTL_MINUTES = 10
SESSION_REQUEST_TTL_MINUTES = 10
SESSION_MAX_TTL_MINUTES = 30
MAX_EXCERPT_LENGTH = 20_000
CAPTURE_MODES = {"manual", "supervised", "approved_session"}
SOURCE_FAMILIES = {"discord", "x", "twitch", "kick", "reddit", "web", "rss"}
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_EXTENSION_ID = re.compile(r"^[a-p]{32}$")


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_domain(value: str) -> str:
    """Reduce a user/agent-supplied domain to a bare public hostname or reject it.

    Rejects schemes, paths, ports, IP literals, and localhost so a session can only ever
    scope to a real public host such as 'discord.com'.
    """
    raw = (value or "").strip().lower()
    if not raw or "/" in raw or ":" in raw or " " in raw:
        raise ValueError("Provide a bare domain such as discord.com, without scheme, port, or path.")
    if raw in {"localhost"} or raw.endswith(".local"):
        raise ValueError("Local hostnames cannot be captured.")
    try:
        ipaddress.ip_address(raw)
        raise ValueError("Provide a domain name, not an IP address.")
    except ValueError as exc:
        if "not an IP address" in str(exc) or "Provide a domain" in str(exc):
            raise
    if "." not in raw or len(raw) < 4 or len(raw) > 253:
        raise ValueError("Provide a valid public domain such as discord.com.")
    return raw


def _host_in_domain(host: str, domain: str) -> bool:
    host = (host or "").lower()
    return host == domain or host.endswith(f".{domain}")


def allowed_local_origin(origin: str, extension_id: str = "") -> bool:
    """Allow the local UI or the exact paired Chrome extension, never arbitrary web pages."""
    if origin in {"http://127.0.0.1", "http://localhost"}:
        return True
    parsed = urlparse(origin)
    return bool(extension_id and parsed.scheme == "chrome-extension" and parsed.hostname == extension_id and not parsed.path.strip("/"))


def _safe_evidence_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Capture URL must be an http or https URL.")
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    query = urlencode([
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ])
    return urlunparse((parsed.scheme, host, parsed.path, parsed.params, query, ""))


def _source_family(value: str) -> str:
    host = (urlparse(value).hostname or "").lower()
    hosts = {
        "discord": ("discord.com", "discordapp.com"), "x": ("x.com", "twitter.com"),
        "twitch": ("twitch.tv",), "kick": ("kick.com",), "reddit": ("reddit.com", "redd.it"),
    }
    for family, domains in hosts.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return family
    return "web"


def _captured_at(value: datetime | str | None) -> datetime | None:
    if not value:
        return None
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    if timestamp.year < 2000 or timestamp > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("captured_at must be a valid past timestamp.")
    return timestamp


class CaptureStore:
    """Persistence and security boundary for supervised browser captures."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def create_pairing_code(self, ttl_minutes: int = PAIRING_TTL_MINUTES) -> dict[str, Any]:
        ttl = max(1, min(int(ttl_minutes), 30))
        code = "-".join((secrets.token_hex(2), secrets.token_hex(2), secrets.token_hex(2))).upper()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl)
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO browser_pairing_codes (code_hash,expires_at) VALUES (%s,%s)",
                (_hash_secret(code), expires_at),
            )
            conn.commit()
        return {"pairing_code": code, "expires_at": expires_at.isoformat()}

    def exchange_pairing_code(self, code: str, *, client_name: str, extension_id: str) -> dict[str, Any]:
        name = client_name.strip()[:120]
        extension_id = extension_id.strip().lower()
        if not name:
            raise ValueError("A client name is required.")
        if not _EXTENSION_ID.fullmatch(extension_id):
            raise ValueError("A valid Chrome extension ID is required.")
        token = secrets.token_urlsafe(32)
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE browser_pairing_codes SET used_at=now()
                   WHERE code_hash=%s AND used_at IS NULL AND expires_at>now()
                   RETURNING id""",
                (_hash_secret(code.strip().upper()),),
            )
            if not cur.fetchone():
                raise ValueError("Pairing code is invalid, expired, or already used.")
            cur.execute(
                """INSERT INTO browser_clients (name,extension_id,token_hash)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (extension_id,name) DO UPDATE
                   SET token_hash=EXCLUDED.token_hash, revoked_at=NULL, last_used_at=NULL
                   RETURNING id""",
                (name, extension_id, _hash_secret(token)),
            )
            client_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO browser_capture_audit (client_id,event_type,details) VALUES (%s,'client.paired',%s)",
                (client_id, json_value({"extension_id": extension_id})),
            )
            conn.commit()
        return {"client_id": client_id, "token": token, "extension_id": extension_id}

    def authenticate(self, token: str, *, origin: str = "") -> dict[str, Any]:
        if not token:
            raise PermissionError("Missing capture token.")
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,name,extension_id,token_hash FROM browser_clients WHERE token_hash=%s AND revoked_at IS NULL",
                (_hash_secret(token),),
            )
            client = cur.fetchone()
            if not client or not hmac.compare_digest(client["token_hash"], _hash_secret(token)):
                raise PermissionError("Capture token is invalid or revoked.")
            if origin and not allowed_local_origin(origin, client["extension_id"]):
                raise PermissionError("This browser origin is not paired with Oyster.")
            cur.execute("UPDATE browser_clients SET last_used_at=now() WHERE id=%s", (client["id"],))
            conn.commit()
        return {"client_id": client["id"], "name": client["name"], "extension_id": client["extension_id"]}

    def revoke_client(self, client_id: int) -> None:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("UPDATE browser_clients SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (client_id,))
            if cur.rowcount != 1:
                raise ValueError("Browser client was not found or is already revoked.")
            cur.execute(
                "INSERT INTO browser_capture_audit (client_id,event_type) VALUES (%s,'client.revoked')", (client_id,)
            )
            conn.commit()

    def mission(self, job_id: int) -> dict[str, Any]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT id,brief,plan FROM research_jobs WHERE id=%s AND status='active'", (job_id,))
            job = cur.fetchone()
        if not job:
            raise ValueError(f"Research job {job_id} was not found.")
        plan = job["plan"]
        terms = list(dict.fromkeys(plan.get("entities_and_terms", []) + [q for values in plan.get("source_queries", {}).values() for q in values]))
        return {
            "job_id": job["id"],
            "brief": job["brief"],
            "research_questions": plan.get("research_questions", []),
            "look_for": terms[:30],
            "exclusions": plan.get("exclusions", []),
            "instructions": "Collect only relevant content currently visible to you; review it before promotion to evidence.",
        }

    def submit(self, client_id: int, *, job_id: int, source_type: str, url: str, excerpt: str,
               page_title: str = "", author: str = "", anonymize: bool = True,
               research_question: str = "", researcher_note: str = "", capture_mode: str = "manual",
               metadata: dict[str, Any] | None = None, captured_at: datetime | str | None = None,
               client_capture_id: str = "") -> dict[str, Any]:
        excerpt, submitted_url = excerpt.strip(), url.strip()
        if not excerpt or len(excerpt) > MAX_EXCERPT_LENGTH:
            raise ValueError(f"Excerpt must be between 1 and {MAX_EXCERPT_LENGTH} characters.")
        url = _safe_evidence_url(submitted_url)
        if capture_mode not in CAPTURE_MODES:
            raise ValueError("Unsupported capture mode.")
        if not source_type.strip():
            raise ValueError("Source type is required.")
        family = _source_family(url)
        captured = _captured_at(captured_at)
        client_capture_id = client_capture_id.strip()[:200] or ""
        alias = f"Participant {hashlib.sha256(f'{job_id}:{author}'.encode()).hexdigest()[:8]}" if anonymize and author else ""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT status,plan FROM research_jobs WHERE id=%s", (job_id,))
            job = cur.fetchone()
            if not job:
                raise ValueError(f"Research job {job_id} was not found.")
            if job["status"] != "active":
                raise ValueError("Captures can only be added to an active research job.")
            questions = job["plan"].get("research_questions", [])
            if research_question and research_question not in questions:
                raise ValueError("Research question must belong to the selected job plan.")
            cur.execute("SELECT 1 FROM browser_clients WHERE id=%s AND revoked_at IS NULL", (client_id,))
            if not cur.fetchone():
                raise PermissionError("Browser client is invalid or revoked.")
            cur.execute(
                """INSERT INTO browser_captures
                   (job_id,client_id,capture_mode,source_type,url,page_title,excerpt,original_author,
                    anonymized_author,anonymize,research_question,researcher_note,source_metadata,captured_at,client_capture_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at,status""",
                (job_id, client_id, capture_mode, family, url, page_title[:500] or None,
                 excerpt, (author[:500] or None) if not anonymize else None, alias or None, anonymize,
                 research_question[:1000] or None, researcher_note[:4000] or None,
                 json_value({**(metadata or {}), "submitted_source_type": source_type}), captured,
                 client_capture_id or None),
            )
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO browser_capture_audit (capture_id,client_id,event_type,details) VALUES (%s,%s,'capture.submitted',%s)",
                (row["id"], client_id, json_value({"mode": capture_mode, "anonymize": anonymize,
                                                   "submitted_source_type": source_type, "submitted_url": submitted_url != url})),
            )
            conn.commit()
        return {"capture_id": row["id"], "status": row["status"], "created_at": row["created_at"].isoformat()}

    def list_pending(self, client_id: int, job_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.expire_pending()
        where, params = "status='pending' AND client_id=%s", [client_id]
        if job_id is not None:
            where += " AND job_id=%s"
            params.append(job_id)
        params.append(max(1, min(int(limit), 200)))
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT id,job_id,capture_mode,source_type,url,page_title,excerpt,
                    CASE WHEN anonymize THEN anonymized_author ELSE original_author END AS author,
                    anonymize,research_question,researcher_note,source_metadata,created_at
                    FROM browser_captures WHERE {where} ORDER BY created_at LIMIT %s""", params,
            )
            rows = cur.fetchall()
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in rows]

    def review(self, capture_id: int, *, client_id: int, approve: bool) -> dict[str, Any]:
        """Atomically review once; approval promotes exactly one deduplicated evidence item."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT c.* FROM browser_captures c JOIN research_jobs j ON j.id=c.job_id
                   WHERE c.id=%s AND c.client_id=%s AND j.status='active' FOR UPDATE OF c,j""",
                (capture_id, client_id),
            )
            capture = cur.fetchone()
            if not capture:
                raise ValueError("Capture was not found.")
            if capture["status"] != "pending":
                raise ValueError(f"Capture has already been {capture['status']}.")
            evidence_id = None
            status = "approved" if approve else "rejected"
            if approve:
                author = capture["anonymized_author"] if capture["anonymize"] else capture["original_author"]
                metadata = dict(capture["source_metadata"] or {})
                metadata.update({
                    "capture_id": capture_id, "capture_mode": capture["capture_mode"],
                    "research_question": capture["research_question"], "researcher_note": capture["researcher_note"],
                    "supervised_capture": True, "anonymized": capture["anonymize"],
                    "captured_at": capture["captured_at"].isoformat() if capture["captured_at"] else None,
                    "client_capture_id": capture["client_capture_id"],
                })
                digest = hashlib.sha256(f"{capture['source_type']}\n{capture['url']}\n{capture['excerpt']}".encode()).hexdigest()
                cur.execute(
                    """INSERT INTO research_evidence
                       (job_id,source_type,url,title,excerpt,author,published_at,query,metadata,content_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (job_id,content_hash) DO UPDATE SET collected_at=now()
                       RETURNING id""",
                    (capture["job_id"], capture["source_type"], capture["url"], capture["page_title"],
                     capture["excerpt"], author, capture["captured_at"], capture["research_question"], json_value(metadata), digest),
                )
                evidence_id = cur.fetchone()["id"]
            cur.execute(
                """UPDATE browser_captures SET status=%s,reviewed_at=now(),evidence_id=%s,
                   excerpt=CASE WHEN %s THEN '[promoted to evidence]' ELSE '[rejected]' END,
                   original_author=NULL,anonymized_author=NULL,researcher_note=NULL,source_metadata='{}'::jsonb
                   WHERE id=%s""",
                (status, evidence_id, approve, capture_id),
            )
            cur.execute(
                "INSERT INTO browser_capture_audit (capture_id,client_id,event_type,details) VALUES (%s,%s,%s,%s)",
                (capture_id, capture["client_id"], f"capture.{status}", json_value({"evidence_id": evidence_id})),
            )
            conn.commit()
        return {"capture_id": capture_id, "status": status, "evidence_id": evidence_id}

    def reject_pending(self, job_id: int, client_id: int) -> dict[str, int]:
        """Reject a bounded job queue without touching already-reviewed captures."""
        self.expire_pending()
        where, params = "job_id=%s AND status='pending' AND client_id=%s", [job_id, client_id]
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE browser_captures SET status='rejected',reviewed_at=now(),excerpt='[rejected]',
                    original_author=NULL,anonymized_author=NULL,researcher_note=NULL,source_metadata='{{}}'::jsonb
                    WHERE {where} RETURNING id,client_id""",
                params,
            )
            rows = cur.fetchall()
            if rows:
                cur.executemany(
                    """INSERT INTO browser_capture_audit
                       (capture_id,client_id,event_type,details) VALUES (%s,%s,'capture.rejected',%s)""",
                    [(row["id"], row["client_id"], json_value({"bulk": True})) for row in rows],
                )
            conn.commit()
        return {"rejected": len(rows)}

    def approve_direct(self, client_id: int, *, client_capture_id: str, **capture: Any) -> dict[str, Any]:
        """Atomically submit and approve a client-keyed capture; safe to retry."""
        key = client_capture_id.strip()[:200]
        if not key:
            raise ValueError("client_capture_id is required for direct approval.")
        lock_key = int.from_bytes(hashlib.sha256(f"{client_id}:{key}".encode()).digest()[:8], "big", signed=True)
        excerpt = str(capture.get("excerpt", "")).strip()
        if not excerpt or len(excerpt) > MAX_EXCERPT_LENGTH:
            raise ValueError(f"Excerpt must be between 1 and {MAX_EXCERPT_LENGTH} characters.")
        url = _safe_evidence_url(str(capture.get("url", "")))
        job_id = int(capture["job_id"])
        source_label = str(capture.get("source_type", "web"))
        family = _source_family(url)
        question = str(capture.get("research_question", ""))
        anonymize = bool(capture.get("anonymize", True))
        author = str(capture.get("author", ""))[:500]
        alias = f"Participant {hashlib.sha256(f'{job_id}:{author}'.encode()).hexdigest()[:8]}" if anonymize and author else ""
        captured = _captured_at(capture.get("captured_at"))
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            cur.execute(
                    "SELECT id,status,evidence_id FROM browser_captures WHERE client_id=%s AND client_capture_id=%s",
                    (client_id, key),
            )
            existing = cur.fetchone()
            if existing:
                if existing["status"] != "approved":
                    raise ValueError("client_capture_id already exists but is not approved.")
                return {"capture_id": existing["id"], "status": "approved", "evidence_id": existing["evidence_id"], "idempotent": True}
            cur.execute("SELECT status,plan FROM research_jobs WHERE id=%s FOR UPDATE", (job_id,))
            job = cur.fetchone()
            if not job or job["status"] != "active":
                raise ValueError("Captures can only be added to an active research job.")
            if question and question not in job["plan"].get("research_questions", []):
                raise ValueError("Research question must belong to the selected job plan.")
            cur.execute("SELECT 1 FROM browser_clients WHERE id=%s AND revoked_at IS NULL", (client_id,))
            if not cur.fetchone():
                raise PermissionError("Browser client is invalid or revoked.")
            incoming_meta = dict(capture.get("metadata") or {})
            capture_mode = str(capture.get("capture_mode", "manual"))
            # Consent gate: an approved_session capture is only accepted while an approved,
            # unexpired session for this client/job covers the captured URL's domain.
            session_cap = MAX_EXCERPT_LENGTH
            if capture_mode == "approved_session":
                session_id = incoming_meta.get("session_id")
                host = urlparse(url).hostname or ""
                session_row = None
                if session_id:
                    cur.execute(
                        """SELECT * FROM capture_sessions
                           WHERE id=%s AND status='approved' AND client_id=%s AND job_id=%s
                             AND expires_at > now() FOR UPDATE""",
                        (int(session_id), client_id, job_id),
                    )
                    session_row = cur.fetchone()
                if not session_row or not _host_in_domain(host, session_row["domain"]):
                    raise PermissionError("No approved capture session covers this domain.")
                session_cap = session_row["max_payload_bytes"]
            # Network payloads are promoted to raw_responses (redacted) and reduced to a
            # bounded summary in the evidence metadata, so the evidence row never carries
            # a large or unredacted body.
            network = incoming_meta.pop("network", None)
            if isinstance(network, dict):
                body = network.get("body", "")
                body_bytes = body.encode("utf-8", "replace") if isinstance(body, str) else bytes(body or b"")
                body_bytes = body_bytes[:session_cap]
                raw_id = record_raw_parts(
                    self.database_url, job_id, "browser.approved_session", "browser_network",
                    method=str(network.get("method", "GET")), url=str(network.get("url", url)),
                    status=int(network.get("status", 0) or 0),
                    response_headers={"Content-Type": str(network.get("content_type", ""))},
                    content=body_bytes, conn=conn,
                )
                incoming_meta["network"] = {
                    "url": str(network.get("url", url)), "method": str(network.get("method", "GET")),
                    "status": int(network.get("status", 0) or 0),
                    "content_type": str(network.get("content_type", "")), "raw_response_id": raw_id,
                }
            metadata = {**incoming_meta, "submitted_source_type": source_label,
                        "client_capture_id": key, "captured_at": captured.isoformat() if captured else None,
                        "capture_mode": capture_mode, "research_question": question,
                        "researcher_note": capture.get("researcher_note", ""), "supervised_capture": True,
                        "anonymized": anonymize}
            cur.execute(
                """INSERT INTO browser_captures
                   (job_id,client_id,capture_mode,source_type,url,page_title,excerpt,original_author,
                    anonymized_author,anonymize,research_question,researcher_note,source_metadata,captured_at,
                    client_capture_id,status,reviewed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approved',now()) RETURNING id""",
                (job_id, client_id, capture.get("capture_mode", "manual"), family, url,
                 str(capture.get("page_title", ""))[:500] or None, "[promoted to evidence]", None,
                 None, anonymize, question or None, None, json_value({}), captured, key),
            )
            capture_id = cur.fetchone()["id"]
            metadata["capture_id"] = capture_id
            digest = hashlib.sha256(f"{family}\n{url}\n{excerpt}".encode()).hexdigest()
            cur.execute(
                """INSERT INTO research_evidence
                   (job_id,source_type,url,title,excerpt,author,published_at,query,metadata,content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (job_id,content_hash) DO UPDATE SET collected_at=now() RETURNING id""",
                (job_id, family, url, str(capture.get("page_title", ""))[:500] or None, excerpt,
                 alias if anonymize else (author or None), captured, question or None, json_value(metadata), digest),
            )
            evidence_id = cur.fetchone()["id"]
            cur.execute("UPDATE browser_captures SET evidence_id=%s WHERE id=%s", (evidence_id, capture_id))
            cur.execute(
                """INSERT INTO browser_capture_audit (capture_id,client_id,event_type,details)
                   VALUES (%s,%s,'capture.approved',%s)""",
                (capture_id, client_id, json_value({"evidence_id": evidence_id, "direct": True})),
            )
            conn.commit()
        return {"capture_id": capture_id, "status": "approved", "evidence_id": evidence_id, "idempotent": False}

    def expire_pending(self) -> int:
        """Remove stale content from the review queue while retaining a minimal audit event."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE browser_captures SET status='expired',reviewed_at=now(),excerpt='[expired]',
                   original_author=NULL,anonymized_author=NULL,researcher_note=NULL,source_metadata='{}'::jsonb
                   WHERE status='pending' AND expires_at<=now() RETURNING id,client_id"""
            )
            rows = cur.fetchall()
            if rows:
                cur.executemany(
                    """INSERT INTO browser_capture_audit
                       (capture_id,client_id,event_type) VALUES (%s,%s,'capture.expired')""",
                    [(row["id"], row["client_id"]) for row in rows],
                )
            conn.commit()
        return len(rows)

    def delete_capture(self, capture_id: int, client_id: int) -> dict[str, Any]:
        """Delete promoted evidence or discard staging content, scoped to its paired client."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,status,evidence_id FROM browser_captures WHERE id=%s AND client_id=%s FOR UPDATE",
                (capture_id, client_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Capture was not found.")
            evidence_id = row["evidence_id"]
            cur.execute(
                "INSERT INTO browser_capture_audit (capture_id,client_id,event_type,details) VALUES (%s,%s,'capture.deleted',%s)",
                (capture_id, client_id, json_value({"evidence_id": evidence_id})),
            )
            if evidence_id:
                cur.execute("DELETE FROM research_evidence WHERE id=%s", (evidence_id,))
                cur.execute(
                    """UPDATE browser_captures SET evidence_id=NULL,excerpt='[deleted]',original_author=NULL,
                       anonymized_author=NULL,researcher_note=NULL,source_metadata='{}'::jsonb WHERE id=%s""",
                    (capture_id,),
                )
            else:
                cur.execute("DELETE FROM browser_captures WHERE id=%s", (capture_id,))
            conn.commit()
        return {"capture_id": capture_id, "deleted": True, "evidence_id": evidence_id}

    def audit_log(self, capture_id: int) -> list[dict[str, Any]]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT event_type,details,created_at FROM browser_capture_audit WHERE capture_id=%s ORDER BY created_at,id",
                (capture_id,),
            )
            rows = cur.fetchall()
        return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in rows]

    # --- Consent-gated browser traffic capture sessions -------------------------------

    @staticmethod
    def _session_row(row: dict[str, Any]) -> dict[str, Any]:
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}

    def _audit_session(self, cur: Any, session_id: int, client_id: int | None, event: str, details: dict[str, Any]) -> None:
        cur.execute(
            "INSERT INTO browser_capture_audit (client_id,event_type,details) VALUES (%s,%s,%s)",
            (client_id, event, json_value({"session_id": session_id, **details})),
        )

    def expire_sessions(self) -> int:
        """Expire unapproved requests past their request TTL and approved sessions past expiry."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE capture_sessions SET status='expired', ended_at=now()
                   WHERE (status='requested' AND created_at < now() - make_interval(mins => %s))
                      OR (status='approved' AND expires_at IS NOT NULL AND expires_at <= now())
                   RETURNING id, client_id""",
                (SESSION_REQUEST_TTL_MINUTES,),
            )
            rows = cur.fetchall()
            for row in rows:
                self._audit_session(cur, row["id"], row["client_id"], "session.expired", {})
            conn.commit()
        return len(rows)

    def request_session(self, job_id: int, domain: str, reason: str, requested_by: str = "agent",
                        max_payload_bytes: int = 131072) -> dict[str, Any]:
        domain = _normalize_domain(domain)
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("Give a short reason (1-1000 characters) for the capture session.")
        cap = max(1024, min(int(max_payload_bytes), 1_048_576))
        self.expire_sessions()
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM research_jobs WHERE id=%s", (job_id,))
            job = cur.fetchone()
            if not job:
                raise ValueError(f"Research job {job_id} was not found.")
            if job["status"] != "active":
                raise ValueError("Traffic sessions can only be requested for an active research job.")
            cur.execute(
                """SELECT id FROM capture_sessions
                   WHERE job_id=%s AND domain=%s AND status IN ('requested','approved') LIMIT 1""",
                (job_id, domain),
            )
            if cur.fetchone():
                raise ValueError(f"A capture session for {domain} on job {job_id} is already open.")
            cur.execute(
                """INSERT INTO capture_sessions (job_id,domain,reason,requested_by,max_payload_bytes)
                   VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                (job_id, domain, reason, requested_by.strip()[:60] or "agent", cap),
            )
            row = cur.fetchone()
            self._audit_session(cur, row["id"], None, "session.requested", {"domain": domain, "job_id": job_id})
            conn.commit()
        return self._session_row(row)

    def list_sessions(self, client_id: int | None = None, job_id: int | None = None) -> list[dict[str, Any]]:
        self.expire_sessions()
        where = ["status IN ('requested','approved')"]
        params: list[Any] = []
        if job_id is not None:
            where.append("job_id=%s")
            params.append(job_id)
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM capture_sessions WHERE {' AND '.join(where)} ORDER BY created_at DESC", params)
            rows = cur.fetchall()
        return [self._session_row(row) for row in rows]

    def session_status(self, session_id: int) -> dict[str, Any]:
        self.expire_sessions()
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM capture_sessions WHERE id=%s", (session_id,))
            row = cur.fetchone()
        if not row:
            raise ValueError("Capture session was not found.")
        return self._session_row(row)

    def approve_session(self, session_id: int, client_id: int, ttl_minutes: int = SESSION_MAX_TTL_MINUTES) -> dict[str, Any]:
        ttl = max(1, min(int(ttl_minutes), SESSION_MAX_TTL_MINUTES))
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM browser_clients WHERE id=%s AND revoked_at IS NULL", (client_id,))
            if not cur.fetchone():
                raise PermissionError("Browser client is invalid or revoked.")
            cur.execute(
                """UPDATE capture_sessions
                   SET status='approved', client_id=%s, approved_at=now(),
                       expires_at=now() + make_interval(mins => %s)
                   WHERE id=%s AND status='requested'
                   RETURNING *""",
                (client_id, ttl, session_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Capture session is not awaiting approval.")
            self._audit_session(cur, session_id, client_id, "session.approved",
                                {"domain": row["domain"], "job_id": row["job_id"], "ttl_minutes": ttl})
            conn.commit()
        result = self._session_row(row)
        return {"session_id": result["id"], "job_id": result["job_id"], "domain": result["domain"],
                "expires_at": result["expires_at"], "max_payload_bytes": result["max_payload_bytes"]}

    def decline_session(self, session_id: int, client_id: int) -> dict[str, Any]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE capture_sessions SET status='declined', client_id=%s, ended_at=now() WHERE id=%s AND status='requested' RETURNING *",
                (client_id, session_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Capture session is not awaiting approval.")
            self._audit_session(cur, session_id, client_id, "session.declined", {"domain": row["domain"]})
            conn.commit()
        return {"session_id": session_id, "status": "declined"}

    def stop_session(self, session_id: int, client_id: int | None = None) -> dict[str, Any]:
        """Stop an open session. The local control-center user may stop without a client id."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE capture_sessions SET status='stopped', ended_at=now() WHERE id=%s AND status IN ('requested','approved') RETURNING *",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Capture session is not open.")
            self._audit_session(cur, session_id, client_id if client_id is not None else row["client_id"],
                                "session.stopped", {"domain": row["domain"]})
            conn.commit()
        return {"session_id": session_id, "status": "stopped"}

    def active_session(self, client_id: int, job_id: int, host: str) -> dict[str, Any] | None:
        self.expire_sessions()
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM capture_sessions
                   WHERE status='approved' AND client_id=%s AND job_id=%s
                     AND expires_at > now() ORDER BY approved_at DESC""",
                (client_id, job_id),
            )
            rows = cur.fetchall()
        for row in rows:
            if _host_in_domain(host, row["domain"]):
                return self._session_row(row)
        return None
