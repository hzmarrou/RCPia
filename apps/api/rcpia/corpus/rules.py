"""Routing rules: which clinical situation each passage of a guideline speaks to.

**This is the stage that carries all the clinical judgment**, and the one that decided how far
the corpus got. Four documents were routed by hand and route well. The automated alternative
matched the chunk body — `"metast" in text` tagging a passage that reads *"no evidence of
metastatic disease"* as metastatic — and produced 38 documents nobody could load.

What works is matching the **section path**: the document's own heading hierarchy, written by
its authors, which already encodes the structure a reader navigates by. AFU's bladder guideline
has a section called *"très haut risque"*, and everything beneath it is about very-high-risk
NMIBC. That is not an inference; it is what the heading says.

So a rule is a heading fragment plus the routing it implies, and a document's rules are an
ordered list. Three things follow, and each is enforced rather than trusted:

**Order is correctness.** `"haut risque"` matches inside `"tres haut risque"`, so the specific
rule must come first. In a Python `if` tree this is invisible until someone reorders it; here
the order is the file, and `audit` reports a rule that can never fire because an earlier one
already claimed everything it matches.

**A rule that matches nothing is a bug, not a no-op.** Guidelines are revised annually and
headings get renamed. A rule silently matching nothing leaves its passages on the default,
which looks like successful routing. `audit` refuses it.

**A path that occurs twice must be disambiguated.** Real documents repeat headings — `FOCUS`,
`Modalités de prescription`, `⮱ BENZODIAZEPINES` — sometimes with different content beneath
each. A rule matching both when only one was meant is silent and wrong, so `audit` requires an
explicit `occurrence` wherever the match is ambiguous.

Rules are JSON rather than YAML: every other corpus artefact is JSON, PyYAML is only a
transitive dependency here, and a per-rule `note` carries the human explanation that YAML
comments would have — with the advantage that it survives round-tripping and can be shown in a
review.
"""

import json
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rcpia.corpus.vocabulary import (
    AGENT_ROLES,
    CLINICAL_PURPOSES,
    DISEASE_SETTINGS,
    NOT_SPECIFIED_REASONS,
    RETRIEVAL_EXCLUSION_REASONS,
    THERAPY_LINES,
)

NOT_SPECIFIED = "not_specified"

# The separator used when a nested heading path is flattened for matching. A rule may name
# either a single heading (`"tres haut risque"`) or a path fragment
# (`"Traitements > Insomnie par anxiete"`), and both are substring matches against this.
PATH_SEPARATOR = " > "

_RULE_FIELDS = frozenset(
    {
        "match_section", "setting", "reason", "therapy_line",
        "purposes", "roles", "occurrence", "note",
        "exclude", "exclusion_reason",
    }
)
_FILE_FIELDS = frozenset({"document_id", "default", "rules", "note"})


class RuleError(Exception):
    """A rule file says something that cannot be true, or cannot be checked."""


def normalise(text: str) -> str:
    """Case- and accent-insensitive, whitespace-collapsed.

    Matches `cases.extraction.normalize_label`'s treatment deliberately: headings and labels
    come from the same PDF extraction and carry the same inconsistencies — `é` decomposed or
    precomposed, non-breaking spaces, doubled whitespace around a colon.
    """
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.replace("’", "'").split())


def flatten(section_path: list[str] | tuple[str, ...]) -> str:
    """A heading hierarchy as one normalised string, for substring matching."""
    return PATH_SEPARATOR.join(normalise(part) for part in section_path)


@dataclass(frozen=True)
class Rule:
    """One heading fragment and the routing it implies."""

    match_section: str
    setting: str
    # Required when `setting` is `not_specified`, forbidden otherwise. Absence is a stated
    # choice here, never a shrug: `general_principle` and `not_determinable` mean opposite
    # things about whether the routing did its job.
    reason: str | None = None
    therapy_line: str = "not_applicable"
    purposes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    # Which occurrence of an ambiguous path this rule means, 1-based. `None` is valid only
    # where the path occurs at most once; `audit` decides that against the real document.
    occurrence: int | None = None
    # Kept in the release, never returned by retrieval. The case that forced it: EAU prostate's
    # chapter 9 is 118 passages of references, written as prose rather than as the `| [1] |`
    # tables the split stage recognises by shape. Corpus v4.0 excluded these by matching the
    # section heading, which is a routing decision -- so it belongs in a rule, where the
    # ordering and the audit already apply to it.
    exclude: bool = False
    exclusion_reason: str | None = None
    note: str = ""

    @property
    def needle(self) -> str:
        return normalise(self.match_section)

    def matches(self, flat_path: str) -> bool:
        return self.needle in flat_path


@dataclass(frozen=True)
class RoutingRules:
    """Every rule for one document, in the order they are tried."""

    document_id: str
    default: Rule
    rules: tuple[Rule, ...]
    note: str = ""


@dataclass(frozen=True)
class Decision:
    """What routed a passage, and by which rule — so a wrong answer can be traced to a line."""

    rule: Rule
    matched_by_default: bool

    @property
    def setting(self) -> str:
        return self.rule.setting


@dataclass
class Audit:
    """What the rules do against a document's real headings.

    Not a boolean. A rule file can be structurally perfect and route nothing useful, so the
    audit reports what actually happened and lets the caller decide what is fatal.
    """

    document_id: str
    paths_seen: int = 0
    routed: int = 0
    defaulted: int = 0
    dead: list[Rule] = field(default_factory=list)
    shadowed: list[tuple[Rule, Rule]] = field(default_factory=list)
    ambiguous: list[Rule] = field(default_factory=list)

    @property
    def default_rate(self) -> float:
        """Share of headings no rule claimed. High means the rules are not doing work."""
        return self.defaulted / self.paths_seen if self.paths_seen else 0.0

    @property
    def problems(self) -> list[str]:
        out = [
            f"rule {r.match_section!r} matches no heading in this document — a renamed "
            "heading leaves its passages on the default, which looks like success"
            for r in self.dead
        ]
        out += [
            f"rule {later.match_section!r} can never fire: {earlier.match_section!r} comes "
            "first and already matches everything it would"
            for earlier, later in self.shadowed
        ]
        out += [
            f"rule {r.match_section!r} matches a heading that occurs more than once; set "
            "`occurrence` to say which one is meant"
            for r in self.ambiguous
        ]
        return out


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleError(f"{what} must be an object, not {type(value).__name__}")
    return value


def _string_tuple(value: Any, *, field_name: str, allowed: frozenset[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RuleError(f"{field_name} must be a list of strings")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RuleError(
            f"{field_name} names values this build does not implement: {', '.join(unknown)}. "
            "The vocabulary is owned by rcpia.corpus.vocabulary; a value we do not read is a "
            "decision nobody has made."
        )
    return tuple(value)


def _parse_rule(record: Any, *, where: str, require_match: bool) -> Rule:
    document = _require_mapping(record, where)

    unknown = sorted(set(document) - _RULE_FIELDS)
    if unknown:
        raise RuleError(
            f"{where} carries fields this engine does not implement: {', '.join(unknown)}"
        )

    match_section = document.get("match_section")
    if require_match:
        if not isinstance(match_section, str) or not match_section.strip():
            raise RuleError(f"{where} needs a non-empty `match_section`")
    elif match_section is not None:
        raise RuleError(f"{where} is the default and must not carry `match_section`")

    setting = document.get("setting")
    if not isinstance(setting, str) or setting not in DISEASE_SETTINGS:
        raise RuleError(
            f"{where} has setting {setting!r}, which is not a disease setting this build "
            "implements"
        )

    reason = document.get("reason")
    if setting == NOT_SPECIFIED:
        if not isinstance(reason, str) or reason not in NOT_SPECIFIED_REASONS:
            raise RuleError(
                f"{where} routes to {NOT_SPECIFIED} without a usable `reason`. Guidance that "
                "genuinely spans settings and guidance the rules could not place are different "
                "facts, and collapsing them hides the routing debt."
            )
    elif reason is not None:
        raise RuleError(
            f"{where} carries a `reason` but routes to {setting!r}. A reason explains an "
            "absent setting; attaching one to a present setting states something false."
        )

    therapy_line = document.get("therapy_line", "not_applicable")
    if therapy_line not in THERAPY_LINES:
        raise RuleError(f"{where} has therapy_line {therapy_line!r}, which is not implemented")

    exclude = document.get("exclude", False)
    if not isinstance(exclude, bool):
        raise RuleError(f"{where} has exclude {exclude!r}; it must be true or false")
    exclusion_reason = document.get("exclusion_reason")
    if exclude:
        if exclusion_reason not in RETRIEVAL_EXCLUSION_REASONS:
            raise RuleError(
                f"{where} excludes its passages from retrieval with reason "
                f"{exclusion_reason!r}, which this build does not implement. Choose from "
                f"{', '.join(sorted(RETRIEVAL_EXCLUSION_REASONS))}. A passage removed for an "
                "unreadable reason is removed silently, which is what excluding is meant to "
                "avoid."
            )
    elif exclusion_reason is not None:
        raise RuleError(
            f"{where} carries exclusion_reason {exclusion_reason!r} but does not exclude "
            "anything. A reason attached to a passage that is still retrieved states something "
            "false about it."
        )

    occurrence = document.get("occurrence")
    if occurrence is not None:
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
            raise RuleError(f"{where} has occurrence {occurrence!r}; it must be a 1-based integer")

    return Rule(
        match_section=match_section or "",
        setting=setting,
        reason=reason,
        therapy_line=therapy_line,
        purposes=_string_tuple(
            document.get("purposes"), field_name=f"{where}.purposes", allowed=CLINICAL_PURPOSES
        ),
        roles=_string_tuple(
            document.get("roles"), field_name=f"{where}.roles", allowed=AGENT_ROLES
        ),
        occurrence=occurrence,
        exclude=exclude,
        exclusion_reason=exclusion_reason,
        note=document.get("note") or "",
    )


def parse_rules(document: Any, *, source: str = "rules") -> RoutingRules:
    """Read one document's rule file, refusing anything that cannot be checked."""
    body = _require_mapping(document, source)

    unknown = sorted(set(body) - _FILE_FIELDS)
    if unknown:
        raise RuleError(
            f"{source} carries fields this engine does not implement: {', '.join(unknown)}"
        )

    document_id = body.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        raise RuleError(f"{source} needs a non-empty `document_id`")

    if "default" not in body:
        raise RuleError(
            f"{source} declares no `default`. Without one a heading no rule claims would have "
            "no setting at all, and the file could not say what it believes about the rest of "
            "the document."
        )
    default = _parse_rule(body["default"], where=f"{document_id}.default", require_match=False)

    raw_rules = body.get("rules")
    if not isinstance(raw_rules, list):
        raise RuleError(f"{source} needs `rules` as a list, even when empty")

    rules = tuple(
        _parse_rule(record, where=f"{document_id}.rules[{index}]", require_match=True)
        for index, record in enumerate(raw_rules)
    )

    seen: Counter[tuple[str, int | None]] = Counter(
        (rule.needle, rule.occurrence) for rule in rules
    )
    duplicated = sorted(needle for (needle, _), count in seen.items() if count > 1)
    if duplicated:
        raise RuleError(
            f"{document_id} repeats rules for {', '.join(duplicated)}. The second can never "
            "fire, and which one was meant cannot be recovered from the file."
        )

    return RoutingRules(
        document_id=document_id, default=default, rules=rules, note=body.get("note") or ""
    )


def load_rules(path: Path) -> RoutingRules:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleError(f"{path.name} is not valid JSON: {exc}") from exc
    return parse_rules(document, source=path.name)


def route(
    rules: RoutingRules, section_path: list[str] | tuple[str, ...], *, occurrence: int = 1
) -> Decision:
    """The first rule that matches, or the default.

    `occurrence` is which time this exact heading path has appeared in the document, counting
    from one. A rule pinned to a different occurrence does not match — which is the whole point
    of pinning it.
    """
    flat = flatten(section_path)
    for rule in rules.rules:
        if not rule.matches(flat):
            continue
        if rule.occurrence is not None and rule.occurrence != occurrence:
            continue
        return Decision(rule=rule, matched_by_default=False)
    return Decision(rule=rules.default, matched_by_default=True)


def audit(rules: RoutingRules, section_paths: list[list[str]]) -> Audit:
    """Run the rules against every heading the document actually has.

    `section_paths` is every chunk's path **in document order, duplicates included** — the
    duplicates are the point, since they are what makes a rule ambiguous.
    """
    report = Audit(document_id=rules.document_id, paths_seen=len(section_paths))

    flats = [flatten(path) for path in section_paths]
    occurrences: Counter[str] = Counter()
    seen_index: list[int] = []
    for flat in flats:
        occurrences[flat] += 1
        seen_index.append(occurrences[flat])

    recurring = _recurring_non_contiguously(flats)

    fired: dict[int, list[int]] = {index: [] for index in range(len(rules.rules))}
    for position, (flat, index) in enumerate(zip(flats, seen_index, strict=True)):
        for rule_index, rule in enumerate(rules.rules):
            if not rule.matches(flat):
                continue
            if rule.occurrence is not None and rule.occurrence != index:
                continue
            fired[rule_index].append(position)
            report.routed += 1
            break
        else:
            report.defaulted += 1

    for rule_index, rule in enumerate(rules.rules):
        # Which paths this rule *could* claim, ignoring the rules ahead of it. A rule with no
        # candidates at all is dead; one with candidates that all went to an earlier rule is
        # shadowed, and saying which earlier rule is what makes it fixable.
        candidates = [
            position
            for position, (flat, index) in enumerate(zip(flats, seen_index, strict=True))
            if rule.matches(flat) and (rule.occurrence is None or rule.occurrence == index)
        ]
        if not candidates:
            report.dead.append(rule)
            continue
        if not fired[rule_index]:
            earlier = next(
                (
                    other
                    for other_index, other in enumerate(rules.rules)
                    if other_index < rule_index
                    and any(
                        other.matches(flats[position])
                        and (other.occurrence is None or other.occurrence == seen_index[position])
                        for position in candidates
                    )
                ),
                None,
            )
            if earlier is not None:
                report.shadowed.append((earlier, rule))
            else:
                report.dead.append(rule)
            continue
        # **What the rule actually claimed, not what it could have.** `haut risque` matches
        # inside `tres haut risque` too, but the specific rule above already took those
        # passages — that is the ordering working, not an ambiguity. Judging on candidates
        # would flag every correctly ordered pair of rules in the file.
        claimed = {flats[position] for position in fired[rule_index]}
        if rule.occurrence is None and (
            _spans_unrelated_branches(claimed, rule.needle) or claimed & recurring
        ):
            report.ambiguous.append(rule)

    return report


def _spans_unrelated_branches(matched: set[str], needle: str) -> bool:
    """Whether a rule claims two regions of the document rather than one.

    **Matching many paths is normal and must not be flagged.** A rule naming a heading matches
    that heading and every path beneath it — one section, many chunks — which is the whole point
    of matching a path.

    The test is where the matched paths *diverge*, relative to the heading the rule names. AFU's
    `nouvelles therapies pour les hauts risques` matches both
    `... > immunotherapie systemique` and `... > therapie genique endovesicale`: different
    children, but they share the named heading as their common ancestor, so the rule is claiming
    one region and wants both.

    `FOCUS` under `Fiche 1` and again under `Fiche 2` shares no common ancestor containing the
    needle — the paths diverge *above* the named heading, which is what makes them two different
    places carrying the same label. Only the author knows which was meant.

    An earlier version compared the matched paths to each other and flagged both cases. Run
    against the real AFU fixture it produced two false positives, and a check that fires on
    correct files is a check people learn to skip.
    """
    if len(matched) < 2:
        return False
    common = _common_prefix(matched)
    return needle not in common


def _common_prefix(paths: set[str]) -> str:
    """The deepest heading every path shares, on separator boundaries.

    Boundaries matter: `traitement du cis isole` and `traitement du cis isole recidivant` share
    a string prefix but are different headings, and treating one as an ancestor of the other
    would silently merge two sections.
    """
    split = [path.split(PATH_SEPARATOR) for path in paths]
    shared: list[str] = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        shared.append(parts[0])
    return PATH_SEPARATOR.join(shared)


def _recurring_non_contiguously(flats: list[str]) -> set[str]:
    """Headings that appear in two separate places, rather than once across several chunks.

    **Repetition is normal; separation is the signal.** One section usually spans several
    consecutive chunks, so its path repeats — measured on the ten loaded documents, NCCN
    prostate repeats a path 207 times out of 328. Flagging that would flag everything.

    What is not normal is the same path appearing at positions 65 and 436, which is EAU's
    `5.3.5 Histopathology` — two different parts of the guideline that happen to share a
    heading. A rule naming it takes both and means one. Across all ten documents only 0-8
    headings per document recur this way, so requiring `occurrence` for them costs almost
    nothing.

    **Any gap counts, however small.** A threshold would need a number, and a guessed tolerance
    is how the embedding audit was wrong three times before it was measured. Over-asking is
    cheap here — the author writes `occurrence: 1` — while missing one is silent.
    """
    positions: dict[str, list[int]] = {}
    for index, flat in enumerate(flats):
        positions.setdefault(flat, []).append(index)
    return {
        flat
        for flat, seen in positions.items()
        if any(later - earlier > 1 for earlier, later in zip(seen, seen[1:], strict=False))
    }
