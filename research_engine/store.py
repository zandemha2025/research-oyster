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

    def dossier(self, job_id: int) -> dict[str, Any]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM research_jobs WHERE id=%s", (job_id,))
            job = cur.fetchone()
            if not job:
                raise ValueError(f"Research job {job_id} was not found.")
            cur.execute("SELECT * FROM research_evidence WHERE job_id=%s ORDER BY collected_at DESC", (job_id,))
            evidence = cur.fetchall()
        items = [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items() if key != "content_hash"} for row in evidence]
        counts: dict[str, int] = {}
        for item in items:
            counts[item["source_type"]] = counts.get(item["source_type"], 0) + 1
        return {
            "job": {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in job.items()},
            "evidence": items,
            "coverage": counts,
            "gaps": [source for source in job["plan"]["recommended_sources"] if not counts.get(source)],
        }

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT id,brief,decision,status,created_at,updated_at FROM research_jobs ORDER BY updated_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
        return [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()} for row in rows]

