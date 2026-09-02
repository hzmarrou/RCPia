"""Is this deployment actually able to produce a fiche? Checked, not assumed.

Every item here is something that has already been wrong in a way nothing announced. A stale API
image is healthy and answers `/health` while serving no case routes. A worker starts, stays up,
and cannot find the Pi runtime. Agents run, succeed, and are handed no guideline passages at all.
Each of those looked fine from the outside and was found by accident.

    python -m rcpia.ops.smoke                       # the cheap checks, no model spend
    python -m rcpia.ops.smoke --api http://localhost:8000

Exit code 0 when everything passes, 1 otherwise, so it can gate a build.

The one thing it does **not** do is the upload-to-fiche run: that spends real model time and
needs a dossier, which is a deliberate act. `docs/runbook-smoke.md` carries it as an explicit
step with the commands.
"""

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcpia.agents.factory import get_pi_adapter
from rcpia.agents.pi_adapter import PI_ENTRY, PiNotConfigured, runtime_version
from rcpia.core.config import get_settings

# Routes a current API exposes. Fewer means the image predates the routers -- the failure that
# made an end-to-end verification look like a broken upload for twenty minutes.
REQUIRED_ROUTES = (
    "/cases",
    "/cases/{case_id}/documents",
    "/cases/{case_id}/analysis",
    "/cases/{case_id}/analysis/panel",
    "/cases/{case_id}/pps",
)

PINNED_RUNTIME = "0.80.3"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"  {'PASS' if self.ok else 'FAIL'}  {self.name:34} {self.detail}"


def check_api(base_url: str) -> Check:
    try:
        with urllib.request.urlopen(f"{base_url}/openapi.json", timeout=15) as response:  # noqa: S310
            paths = set(json.load(response)["paths"])
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return Check("api reachable", False, f"{base_url}: {exc}")
    missing = [route for route in REQUIRED_ROUTES if route not in paths]
    if missing:
        return Check(
            "api serves current routes", False,
            f"{len(paths)} routes, missing {', '.join(missing)} -- rebuild the api image",
        )
    return Check("api serves current routes", True, f"{len(paths)} routes")


def check_runtime() -> list[Check]:
    try:
        adapter = get_pi_adapter()
    except PiNotConfigured as exc:
        return [Check("pi runtime configured", False, str(exc))]
    try:
        launch, package = adapter.launcher()
    except PiNotConfigured as exc:
        return [Check("pi runtime resolvable", False, str(exc))]

    version = runtime_version(package)
    checks = [
        Check(
            "pi entry point resolves", package is not None and (package / PI_ENTRY).is_file(),
            str(package),
        ),
        Check(
            f"pi runtime is {PINNED_RUNTIME}", version == PINNED_RUNTIME,
            version or "unknown",
        ),
    ]

    command = adapter.build_command(
        settings=type("S", (), {"provider": "p", "model": "m"})(),
        session_path=adapter.work_root / "smoke.jsonl",
        task_path=adapter.work_root / "smoke.json",
    )
    environment = adapter._environment()
    checks += [
        # Without this the agent runs with no clinical brain and still answers plausibly.
        Check("agents load their brain", "--approve" in command, "--approve present"),
        Check(
            "credential is not in argv",
            not any(adapter.api_key and adapter.api_key in part for part in command),
            "argv clean",
        ),
        Check(
            "pi config is process-local",
            "PI_CODING_AGENT_DIR" in environment
            and not environment["PI_CODING_AGENT_DIR"].startswith("/data"),
            environment.get("PI_CODING_AGENT_DIR", "unset"),
        ),
    ]
    try:
        budget = adapter.budget()
        checks.append(Check(
            "timeouts nest", True,
            f"provider {budget.provider_seconds:.0f}s < process {budget.process_seconds:.0f}s "
            f"< lease {budget.lease_seconds:.0f}s",
        ))
    except ValueError as exc:
        checks.append(Check("timeouts nest", False, str(exc)))
    checks.append(check_calculators())
    return checks


def check_calculators() -> Check:
    """A calculator an agent cannot reach is the failure this project keeps repeating.

    The agent brains, the retriever and the narrative extraction pass were each built, tested
    and left unconnected, and each cost weeks before anyone noticed. None of them announced
    itself: the system produced confident output that merely happened to be poorer. So the
    check is not "do the nomograms import" but "does a patient the models fit actually reach
    them, through the same call the bundle makes".
    """
    from rcpia.nomograms import evaluate_all

    # A prostate patient with every Briganti 2012 input present. If this yields no figure,
    # either the wiring is broken or the models stopped applying to the patients they are for.
    facts = {
        "age": "70",
        "psa_total": "7.53",
        "gleason": "7 (3+4)",
        "ct_dre": "T2a",
        "cores_positive": "5",
        "cores_total": "21",
        "hormone_therapy": "Non",
        "radiation_therapy": "Non",
    }
    try:
        results = evaluate_all(facts, cancer_type="prostate")
    except Exception as exc:  # noqa: BLE001 - a smoke check reports, it does not raise
        return Check("calculators reachable", False, f"{type(exc).__name__}: {exc}")

    computed = [item for item in results if item.estimates]
    if not results:
        return Check("calculators reachable", False, "no calculator answered at all")
    if not computed:
        return Check(
            "calculators reachable",
            False,
            "every calculator refused a complete patient: "
            + "; ".join(item.explain()[:70] for item in results),
        )
    return Check(
        "calculators reachable",
        True,
        ", ".join(
            f"{item.model_id} {sorted(item.estimates.values())[0]:g}%" for item in computed
        ),
    )


async def check_database(session: AsyncSession) -> list[Check]:
    orphaned = await session.scalar(
        sa.text(
            "SELECT count(*) FROM analysis_jobs "
            "WHERE status='running' AND lease_expires_at IS NULL"
        )
    )
    recent = (
        await session.execute(
            sa.text(
                "SELECT agent_role, status, "
                "  coalesce(jsonb_array_length(bundle->'inputs'->'guideline_passages'), 0) AS n "
                "FROM agent_runs WHERE agent_role <> 'rcp_synthesis' "
                "ORDER BY created_at DESC LIMIT 5"
            )
        )
    ).all()

    checks = [
        Check(
            "no unrecoverable running jobs", (orphaned or 0) == 0,
            f"{orphaned} running with no lease" if orphaned else "none",
        )
    ]
    if not recent:
        checks.append(Check("recent agents were grounded", False, "no agent runs to inspect"))
    else:
        ungrounded = [row for row in recent if row.status == "succeeded" and row.n == 0]
        checks.append(Check(
            "recent agents were grounded", not ungrounded,
            f"last {len(recent)} runs: passages "
            + ", ".join(str(row.n) for row in recent)
            + ("  <- a succeeded run with zero passages means the worker has no retriever"
               if ungrounded else ""),
        ))
    recent_extractions = (
        await session.execute(
            sa.text(
                "SELECT payload->>'narrative_ran' AS ran "
                "FROM audit_events WHERE event_type = 'case.facts_extracted' "
                "ORDER BY created_at DESC LIMIT 5"
            )
        )
    ).all()
    if not recent_extractions:
        checks.append(
            Check("extraction read the prose too", False, "no extractions to inspect")
        )
    else:
        # `narrative_ran` is false when no model was asked at all. The deployed worker passed
        # no adapter for fifteen dossiers and the audit record showed zero fields wanted and
        # zero filled, which is exactly what a dossier whose form answered everything looks
        # like. The two are now distinguishable, and this is the check that reads it.
        never_ran = [row for row in recent_extractions if row.ran != "true"]
        checks.append(
            Check(
                "extraction read the prose too",
                not never_ran,
                f"last {len(recent_extractions)} extractions: "
                + ", ".join(str(row.ran) for row in recent_extractions)
                + ("  <- false means the narrative second pass never ran, so a diagnosis "
                   "written in a sentence was never read"
                   if never_ran else ""),
            )
        )
    return checks


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args(argv)

    checks: list[Check] = []
    if not args.skip_api:
        checks.append(check_api(args.api))
    checks += check_runtime()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            checks += await check_database(session)
    except Exception as exc:  # noqa: BLE001 - any database failure is the answer
        checks.append(Check("database reachable", False, str(exc)[:120]))
    finally:
        await engine.dispose()

    print("RCPia smoke check")
    for check in checks:
        print(check.line())
    failed = [check for check in checks if not check.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    if failed:
        print("\nNot ready. The upload-to-fiche step in docs/runbook-smoke.md will not work "
              "until these pass.")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
