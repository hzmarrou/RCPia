"""Opt-in paired V1/V2 execution of synthetic bundles through the unchanged Pi adapter.

This command makes provider calls. It never submits a case, changes a database,
retrieves additional evidence, or retries a rejected output. Results include
production-contract acceptance, NOT an automated verdict on semantic grounding.
"""

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rcpia.agents.pi_adapter import (
    AgentSettings,
    PiAdapter,
    PiNotConfigured,
    PiOutputInvalid,
    PiRunFailed,
    runtime_version,
)
from rcpia.agents.schemas import SynthesisOutput
from rcpia.agents.service import _validated_output
from rcpia.ops.agent_brains import PROMOTIONS
from rcpia.ops.brain_v2_cases import shadow_cases, synthesis_availability_errors


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


async def run(args: argparse.Namespace) -> None:
    args.output = args.output.resolve()
    key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    base_url = os.environ.get("AZURE_OPENAI_BASE_URL", "")
    if not key or not base_url:
        raise PiNotConfigured("Set AZURE_OPENAI_API_KEY and AZURE_OPENAI_BASE_URL.")
    cases = shadow_cases()
    if args.case:
        requested = set(args.case)
        unknown = requested - {case.case_id for case in cases}
        if unknown:
            raise ValueError("Unknown synthetic cases: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case.case_id in requested]
    roots = {"v1": args.v1_agents.resolve(), "v2": args.v2_agents.resolve()}
    manifest: dict[str, Any] = {"arms": {}, "cases": []}
    for arm, root in roots.items():
        manifest["arms"][arm] = {}
        for role in PROMOTIONS:
            folder = root / role
            settings = AgentSettings.from_folder(folder)
            manifest["arms"][arm][role] = {
                "provider": settings.provider,
                "model": settings.model,
                "brain_sha256": _hash((folder / "brain.md").read_text(encoding="utf-8")),
                "append_sha256": _hash(
                    (folder / ".pi" / "APPEND_SYSTEM.md").read_text(encoding="utf-8"),
                ),
            }
    for role in PROMOTIONS:
        for field in ("provider", "model", "append_sha256"):
            if manifest["arms"]["v1"][role][field] != manifest["arms"]["v2"][role][field]:
                raise ValueError(f"Non-brain difference between arms: {role}.{field}")
    for case in cases:
        manifest["cases"].append({
            "case_id": case.case_id,
            "bundle_sha256": _hash(case.bundle.model_dump_json(by_alias=True)),
            "expected": case.expected,
        })
    # Exclusive creation prevents accidentally replaying a completed comparison.
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "manifest.json").write_text(_json(manifest), encoding="utf-8")
    for case in cases:
        for arm in args.arm or ("v1", "v2"):
            role = case.bundle.inputs.agent_role
            adapter = PiAdapter(
                agents_root=roots[arm],
                work_root=args.output / "runs" / arm,
                api_key=key,
                base_url=base_url,
                pi_command=(args.pi_bin,),
            )
            _, package = adapter.launcher()
            record: dict[str, Any] = {
                "case_id": case.case_id, "arm": arm, "role": role,
                "bundle_sha256": _hash(case.bundle.model_dump_json(by_alias=True)),
                "runtime_version": runtime_version(package), "expected": case.expected,
                "contract_valid": False, "semantic_assessment": "pending review",
            }
            try:
                result = await adapter.run(
                    bundle=case.bundle,
                    agent_name=role,
                    case_id=uuid.UUID(case.bundle.inputs.case_id),
                )
            except (PiRunFailed, PiNotConfigured) as exc:
                record["runtime_error"] = str(exc).replace(key, "<redacted>")
            else:
                record.update(
                    duration_seconds=result.duration_seconds,
                    usage=asdict(result.usage),
                    model=result.model,
                    provider=result.provider,
                    session_path=str(result.session_path),
                    final_output_text=result.output_text,
                )
                try:
                    output = _validated_output(result, case.bundle)
                except PiOutputInvalid as exc:
                    record["contract_error"] = str(exc)
                else:
                    record["contract_valid"] = True
                    record["output"] = output.model_dump(mode="json")
                    if isinstance(output, SynthesisOutput):
                        record["synthesis_availability_errors"] = synthesis_availability_errors(
                            output, case.bundle,
                        )
            target = args.output / f"{case.case_id}.{arm}.json"
            target.write_text(_json(record), encoding="utf-8")
            print(
                f"{case.case_id} {arm}: contract_valid={record['contract_valid']}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-agents", type=Path, required=True)
    parser.add_argument("--v2-agents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pi-bin", default="pi")
    parser.add_argument("--case", action="append")
    parser.add_argument("--arm", choices=("v1", "v2"), action="append")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
