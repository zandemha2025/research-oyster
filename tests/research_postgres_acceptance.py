"""Repeatable Postgres acceptance for generalized Research Oyster persistence."""

from pathlib import Path
import subprocess
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.queries import connect, migrate, seed_config
from research_engine.planner import build_plan
from research_engine.store import ResearchStore


def check(condition: bool, story_id: str, detail: str) -> None:
    if not condition:
        raise AssertionError(f"{story_id} failed: {detail}")
    print(f"PASS {story_id} {detail}")


def main() -> None:
    db_name = f"oyster_story_{uuid.uuid4().hex[:12]}"
    subprocess.run(["createdb", db_name], check=True)
    url = f"postgresql://localhost/{db_name}"
    try:
        with connect(url) as conn:
            migrate(conn)
            seed_config(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('research_jobs') AS jobs, to_regclass('research_evidence') AS evidence")
                schema = cur.fetchone()
            check(bool(schema["jobs"] and schema["evidence"]), "STR-01", "research schema exists")

        store = ResearchStore(url)
        plan = build_plan(
            "Understand how Canadian electric vehicle owners discuss winter range anxiety",
            decision="Choose campaign proof points", market="Canada", required_sources=["discord"],
        )
        created = store.create_job(plan["objective"], plan["decision"], plan["market"], plan["time_horizon"], plan)
        check(created["job_id"] > 0 and created["plan"]["market"] == "Canada", "MCP-04", "job and plan persisted")

        first = store.add_evidence(
            created["job_id"], source_type="web", url="https://example.test/ev",
            title="Winter range", excerpt="Owners report range anxiety below freezing.",
            query="EV winter range", metadata={"sample": True},
        )
        second = store.add_evidence(
            created["job_id"], source_type="web", url="https://example.test/ev",
            title="Winter range", excerpt="Owners report range anxiety below freezing.",
            query="EV winter range", metadata={"sample": True},
        )
        check(first["evidence_id"] == second["evidence_id"], "STR-02", "duplicate evidence reused one row")

        dossier = store.dossier(created["job_id"])
        check(
            len(dossier["evidence"]) == 1 and dossier["coverage"] == {"web": 1}
            and dossier.get("synthesis") is None and "content_hash" not in dossier["evidence"][0],
            "MCP-07", "dossier coverage is correct and synthesis is empty before authoring",
        )
        evidence = dossier["evidence"][0]
        check(
            all(evidence.get(key) is not None for key in ("source_type", "url", "title", "excerpt", "collected_at", "query", "metadata")),
            "STR-03", "evidence provenance is retained",
        )
        store.save_synthesis(created["job_id"], {
            "executive_answer": "Owners tolerate winter range loss when preconditioning is easy.",
            "themes": [{"title": "Preconditioning", "insight": "Cited as the main mitigation.",
                        "citations": [{"quote": "range anxiety below freezing", "url": "https://example.test/ev"}]}],
            "recommendations": ["Lead with preconditioning proof points."],
            "confidence": "Moderate; single illustrative source in this test.",
            "limitations": "Private owner groups were out of scope.",
        })
        synthesized = store.dossier(created["job_id"])
        check(
            synthesized["synthesis"]["executive_answer"].startswith("Owners tolerate")
            and synthesized["job"]["status"] == "synthesized",
            "MCP-08", "authored synthesis round-trips into the dossier",
        )
        jobs = store.list_jobs(20)
        check(jobs[0]["id"] == created["job_id"] and jobs[0]["brief"] == plan["objective"], "MCP-06", "jobs resume newest first")
        try:
            store.dossier(99999999)
        except ValueError as exc:
            check("not found" in str(exc), "MCP-07", "unknown job error is actionable")
        else:
            raise AssertionError("MCP-07 failed: unknown job should fail")
    finally:
        subprocess.run(["dropdb", db_name], check=False)


if __name__ == "__main__":
    main()
