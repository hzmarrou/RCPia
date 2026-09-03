"""What a release already routed, committed so the rules can be checked against it.

**The rules were written to reproduce something, and nothing in the repository remembers what.**
Step 4 re-expressed the hand-written routing as rule files and proved, against
`rcpia-corpus-v4.0`, that the settings come out identical. That proof ran once, on a machine
with OneDrive mounted. CI has neither the release nor the sources, so without a committed
record the guarantee decays into a claim in a commit message.

So the expected setting for every passage is written down beside the headings it was derived
from. A rule edited later either still reproduces the baseline or names the passages it moved,
and moving passages is allowed provided it is a diff someone approved rather than a side effect
nobody saw.

The file is position-aligned with the section fixture rather than keyed by path, because paths
repeat: EAU has one heading appearing at positions 65 and 436, and a mapping would silently
collapse the two. `compare` refuses a baseline whose length does not match the fixture, which is
the failure alignment can produce and the only one it can hide.
"""

import json
from pathlib import Path

from rcpia.corpus.rules import RoutingRules, route
from rcpia.corpus.sections import fixture_path, read_fixture

BASELINE_FILE_SUFFIX = ".routing.jsonl"


class BaselineError(Exception):
    """A baseline cannot be read, or does not line up with the headings it describes."""


def baseline_path(directory: Path, document_id: str) -> Path:
    return directory / f"{document_id}{BASELINE_FILE_SUFFIX}"


def routed_settings(chunks: list[dict[str, object]]) -> list[tuple[str, str | None]]:
    """The setting each chunk carries in a release, in order.

    A reason is read only where the setting is absent. Releases have carried a reason of
    `not_applicable` alongside a real setting, which says nothing and would make the baseline
    disagree with rules that correctly carry no reason at all.
    """
    out: list[tuple[str, str | None]] = []
    for index, chunk in enumerate(chunks):
        setting = chunk.get("disease_setting")
        if not isinstance(setting, str):
            raise BaselineError(f"chunk {index} has no disease_setting to record")
        reason = chunk.get("not_specified_reason") if setting == "not_specified" else None
        out.append((setting, reason if isinstance(reason, str) else None))
    return out


def write_baseline(settings: list[tuple[str, str | None]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for setting, reason in settings:
            handle.write(json.dumps([setting, reason], ensure_ascii=False) + "\n")


def read_baseline(source: Path) -> list[tuple[str, str | None]]:
    if not source.exists():
        raise BaselineError(
            f"{source.name} is missing. Without it the rules for this document are checked for "
            "being well-formed and not for being correct, which is the weaker half."
        )
    out: list[tuple[str, str | None]] = []
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BaselineError(f"{source.name} line {number} is not valid JSON: {exc}") from exc
        if (
            not isinstance(record, list)
            or len(record) != 2
            or not isinstance(record[0], str)
            or not (record[1] is None or isinstance(record[1], str))
        ):
            raise BaselineError(
                f"{source.name} line {number} is {record!r}; each line must be "
                "[setting, reason-or-null]"
            )
        out.append((record[0], record[1]))
    if not out:
        raise BaselineError(f"{source.name} holds no settings, so it proves nothing")
    return out


def compare(
    rules: RoutingRules, *, sections: Path, baselines: Path
) -> list[tuple[int, list[str], tuple[str, str | None], tuple[str, str | None]]]:
    """Every passage where the rules and the recorded release disagree.

    Returns `(position, section_path, expected, actual)`. An empty list is the whole claim of
    step 4: this rule file reproduces routing that was written by hand, one document at a time.
    """
    paths = read_fixture(fixture_path(sections, rules.document_id))
    expected = read_baseline(baseline_path(baselines, rules.document_id))
    if len(paths) != len(expected):
        raise BaselineError(
            f"{rules.document_id}: {len(paths)} headings but {len(expected)} recorded settings. "
            "The two files describe the same passages in the same order, so a length mismatch "
            "means one was regenerated without the other and neither can be trusted."
        )

    # Keyed on the path as a tuple rather than a joined string: any separator can occur
    # inside a heading, and two different paths colliding on one key would count
    # occurrences wrongly for both.
    occurrences: dict[tuple[str, ...], int] = {}
    differences = []
    for position, (path, want) in enumerate(zip(paths, expected, strict=True)):
        key = tuple(path)
        occurrences[key] = occurrences.get(key, 0) + 1
        decision = route(rules, path, occurrence=occurrences[key])
        got = (decision.setting, decision.rule.reason)
        if got != want:
            differences.append((position, path, want, got))
    return differences
