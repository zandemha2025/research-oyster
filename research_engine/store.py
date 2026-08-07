from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from db.queries import connect, json_value


class ResearchStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def create_job(self, brief: str, decision: str, market: str, time_horizon: str, plan: dict[str, Any]) -> dict[str, Any]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO research_jobs (brief, decision, market, time_horizon, plan)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id, created_at""",
                (brief, decision or None, market or None, time_horizon or None, json_value(plan)),
            )
            row = cur.fetchone()
            conn.commit()
        return {"job_id": row["id"], "created_at": row["created_at"].isoformat(), "plan": plan}

    def add_evidence(self, job_id: int, *, source_type: str, url: str, title: str, excerpt: str,
                     author: str = "", published_at: datetime | None = None, query: str = "",
                     metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        digest = hashlib.sha256(f"{source_type}\n{url}\n{excerpt}".encode()).hexdigest()
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO research_evidence
                   (job_id,source_type,url,title,excerpt,author,published_at,query,metadata,content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (job_id,content_hash) DO UPDATE SET collected_at=now()
                   RETURNING id, collected_at""",
                (job_id, source_type, url, title or None, excerpt, author or None, published_at,
                 query or None, json_value(metadata or {}), digest),
            )
            row = cur.fetchone()
            conn.commit()
        return {"evidence_id": row["id"], "collected_at": row["collected_at"].isoformat()}

    def save_synthesis(self, job_id: int, synthesis: dict[str, Any]) -> dict[str, Any]:
        """Persist the model-authored report (executive answer, themes, tensions, etc.).

        This is the deliverable. Stored on the job so export leads with the answer and the
        evidence rows are demoted to an appendix. Also marks the job 'synthesized'.
        """
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM research_jobs WHERE id=%s", (job_id,))
            if not cur.fetchone():
                raise ValueError(f"Research job {job_id} was not found.")
            cur.execute(
                "UPDATE research_jobs SET synthesis=%s, status='synthesized', updated_at=now() WHERE id=%s",
                (json_value(synthesis), job_id),
            )
            conn.commit()
        return {"job_id": job_id, "saved": True}

    def record_source_run(self, job_id: int, connector: str, query: str = "", *,
                          outcome: str, yield_count: int = 0, http_status: int | None = None,
                          error_text: str = "", raw_response_id: int | None = None) -> None:
        """Record one collection attempt's real outcome. This is the ledger the synthesis
        must be held to — a source that ran and yielded nothing is 'empty'/'error', never
        silently reportable as 'not available'."""
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO research_source_runs
                   (job_id, connector, query, outcome, http_status, yield_count, error_text, raw_response_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (job_id, connector, query or None, outcome, http_status, yield_count,
                 (error_text or None), raw_response_id),
            )
            conn.commit()

    def dossier(self, job_id: int) -> dict[str, Any]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM research_jobs WHERE id=%s", (job_id,))
            job = cur.fetchone()
            if not job:
                raise ValueError(f"Research job {job_id} was not found.")
            cur.execute("SELECT * FROM research_evidence WHERE job_id=%s ORDER BY collected_at DESC", (job_id,))
            evidence = cur.fetchall()
            cur.execute(
                """SELECT connector, query, outcome, http_status, yield_count, error_text, attempted_at
                   FROM research_source_runs WHERE job_id=%s ORDER BY attempted_at, id""",
                (job_id,),
            )
            source_runs = cur.fetchall()
        items = [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items() if key != "content_hash"} for row in evidence]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["source_type"]] = counts.get(item["source_type"], 0) + 1
        job_view = {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in job.items()}
        runs = [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()} for row in source_runs]
        # Coverage is informational for the model (where has it looked, how much), not a
        # definition of "done" — a job is done when the synthesis answers the question.
        # source_runs is the authoritative ledger: every attempt and its real outcome, so
        # the synthesis cannot report a failed/empty source as merely "not available".
        return {
            "job": job_view,
            "evidence": items,
            "coverage": counts,
            "source_runs": runs,
            "synthesis": job_view.get("synthesis"),
        }

    def list_raw_responses(self, job_id: int) -> list[dict[str, Any]]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, collector, payload_kind, request_method, request_url, response_status,
                          response_headers, body, body_text, body_hash, received_at
                   FROM raw_responses WHERE research_job_id=%s ORDER BY received_at, id""",
                (job_id,),
            )
            rows = cur.fetchall()
        return [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()} for row in rows]

    def search_evidence(self, job_id: int, query: str, limit: int = 25) -> list[dict[str, Any]]:
        """Keyword search over a job's collected evidence (quote text, title, or author). Lets the
        agent pull the specific rows that mention a term instead of re-reading the whole dossier —
        the read path the corpus was missing."""
        like = f"%{(query or '').strip()}%"
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, source_type, url, title, excerpt, author, metadata, collected_at
                   FROM research_evidence
                   WHERE job_id=%s AND (excerpt ILIKE %s OR title ILIKE %s OR author ILIKE %s)
                   ORDER BY collected_at DESC LIMIT %s""",
                (job_id, like, like, like, max(1, min(limit, 100))),
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            meta = r.get("metadata") or {}
            out.append({"id": r["id"], "source_type": r["source_type"], "url": r["url"],
                        "title": r["title"], "author": r["author"],
                        "excerpt": (r["excerpt"] or "")[:400],
                        "metrics": meta.get("metrics") if isinstance(meta, dict) else None,
                        "entity": meta.get("entity") if isinstance(meta, dict) else None})
        return out

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT id,brief,decision,status,created_at,updated_at FROM research_jobs ORDER BY updated_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
        return [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()} for row in rows]

