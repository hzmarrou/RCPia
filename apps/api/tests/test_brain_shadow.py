"""Paired-run orchestration tests; no provider calls or clinical behavior claims."""

import argparse
import json
from pathlib import Path

import pytest

from rcpia.agents.pi_adapter import PiAdapter, PiRunResult
from rcpia.agents.schemas import AgentOutput, TaskBundle
from rcpia.ops import brain_shadow
from rcpia.ops.agent_brains import PROMOTIONS


@pytest.fixture
def shadow_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.invalid")
    for arm in ("v1", "v2"):
        for role in PROMOTIONS:
            folder = tmp_path / arm / role
            (folder / ".pi").mkdir(parents=True)
            (folder / "brain.md").write_text(f"Synthetic {arm} brain.", encoding="utf-8")
            (folder / ".pi" / "APPEND_SYSTEM.md").write_text("Read brain.", encoding="utf-8")
            (folder / ".pi" / "settings.json").write_text(
                json.dumps({"defaultProvider": "synthetic", "defaultModel": "synthetic"}),
                encoding="utf-8",
            )
    return argparse.Namespace(
        v1_agents=tmp_path / "v1",
        v2_agents=tmp_path / "v2",
        output=tmp_path / "results",
        pi_bin="synthetic",
        case=[brain_shadow.shadow_cases()[0].case_id],
        arm=None,
    )


async def test_same_bundle_reaches_both_arms_and_semantics_remain_unassessed(
    shadow_args: argparse.Namespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.chdir(shadow_args.output.parent)
    shadow_args.output = Path("relative-results")

    async def fake_run(
        self: PiAdapter, *, bundle: TaskBundle, agent_name: str, case_id: object,
    ) -> PiRunResult:
        assert self.work_root.is_absolute()
        assert self.agents_root.is_absolute()
        seen.append(bundle.model_dump_json(by_alias=True))
        output = AgentOutput(
            agent_role=agent_name,
            case_id=str(case_id),
            recommendation="Synthetic withholding.",
            recommendation_withheld=True,
            uncertainty_notes=["Synthetic missing evidence."],
            rationale="Synthetic explanation.",
        )
        from rcpia.agents.pi_adapter import PiUsage

        return PiRunResult(
            agent_name=agent_name, output_text=output.model_dump_json(),
            session_path=self.work_root / "synthetic.jsonl", session_bytes=b"",
            provider="synthetic", model="synthetic", runtime_version=None,
            usage=PiUsage(), duration_seconds=0.0,
        )

    monkeypatch.setattr(PiAdapter, "launcher", lambda self: (["synthetic"], None))
    monkeypatch.setattr(PiAdapter, "run", fake_run)
    await brain_shadow.run(shadow_args)
    assert len(seen) == 2
    assert seen[0] == seen[1]
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in shadow_args.output.glob("*.json")
        if path.name != "manifest.json"
    ]
    assert {record["arm"] for record in records} == {"v1", "v2"}
    assert all(record["contract_valid"] for record in records)
    assert all(record["semantic_assessment"] == "pending review" for record in records)
    with pytest.raises(FileExistsError):
        await brain_shadow.run(shadow_args)
    assert len(seen) == 2


async def test_non_brain_settings_difference_stops_before_any_run(
    shadow_args: argparse.Namespace,
) -> None:
    (shadow_args.v2_agents / "medical_oncology" / ".pi" / "settings.json").write_text(
        json.dumps({"defaultProvider": "different", "defaultModel": "synthetic"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Non-brain difference"):
        await brain_shadow.run(shadow_args)
    assert not shadow_args.output.exists()


async def test_unknown_case_stops_without_creating_results(
    shadow_args: argparse.Namespace,
) -> None:
    shadow_args.case = ["not-a-fixture"]
    with pytest.raises(ValueError, match="Unknown synthetic cases"):
        await brain_shadow.run(shadow_args)
    assert not shadow_args.output.exists()
