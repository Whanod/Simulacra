"""Phase 1.5.3 calibration-claim gate tests.

Plan ref: solana-plans/phase-1.5.md, US-003 acceptance criteria.

The gate allows in-progress Phase 2 development on synthetic/recent data, but
refuses to admit a 2.x story to done until ``solana-plans/phase-1.5-decision.md``
exists with a frontmatter ``outcome`` of ``GO`` or ``PIVOT``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_2_PATH = REPO_ROOT / "solana-plans" / "phase-2.md"
DECISION_PATH = REPO_ROOT / "solana-plans" / "phase-1.5-decision.md"
CALIBRATION_GATE_PATH = REPO_ROOT / "solana-plans" / "phase-2-entry-gate.md"

VALID_OUTCOMES = {"GO", "PIVOT", "KILL"}
ADMITTED_OUTCOMES = {"GO", "PIVOT"}
GATING_STATUSES = {"done"}
DECISION_STALENESS_DAYS = 365
CALIBRATION_GATE_REQUIRED_KEYS = (
    "provider",
    "corpus_scope",
)

_STORY_HEADER_RE = re.compile(r"^###\s+(US-\d+)(?::\s*(.+?))?\s*$")
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*([A-Za-z0-9_-]+)")


def _walk_phase_2_stories(text: str) -> list[tuple[str, str, str]]:
    """Return ``(story_id, title, status)`` for every ### US-XXX block in ``text``.

    A status line is the first ``**Status:** <value>`` encountered after
    the story header. Stories without a status line get ``""``.
    """
    stories: list[tuple[str, str, str]] = []
    current_id: str | None = None
    current_title: str = ""
    current_status: str = ""
    have_status = False

    def flush() -> None:
        if current_id is not None:
            stories.append((current_id, current_title, current_status))

    for line in text.splitlines():
        header = _STORY_HEADER_RE.match(line)
        if header:
            flush()
            current_id = header.group(1)
            current_title = (header.group(2) or "").strip()
            current_status = ""
            have_status = False
            continue
        if current_id is not None and not have_status:
            status_match = _STATUS_RE.match(line)
            if status_match:
                current_status = status_match.group(1).strip().lower()
                have_status = True
    flush()
    return stories


def _strip_inline_comment(value: str) -> str:
    """Drop a `# ...` trailing comment, ignoring `#` inside quoted strings."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return value[:idx]
    return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_decision_frontmatter(text: str) -> dict[str, Any]:
    """Dependency-free parser for the decision-file frontmatter contract.

    Supports:
      - top-level scalar keys (``outcome: GO``)
      - explicit nulls (``phase_2_scope_changes: null`` or ``~``)
      - block sequences of mappings (``- story: "2.3"`` / ``  change: ...``)

    Returns ``{}`` if the file lacks a leading ``---`` frontmatter block.
    """
    if not text.startswith("---"):
        return {}
    first_nl = text.find("\n")
    if first_nl == -1:
        return {}
    end = text.find("\n---", first_nl)
    if end == -1:
        return {}
    body = text[first_nl + 1 : end]
    lines = body.splitlines()

    out: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line.startswith((" ", "\t")) or stripped.startswith("- "):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value = _strip_inline_comment(raw_value).strip()
        if value == "":
            items: list[dict[str, Any]] = []
            i += 1
            while i < len(lines):
                sub = lines[i]
                sub_stripped = sub.strip()
                if not sub_stripped or sub_stripped.startswith("#"):
                    i += 1
                    continue
                if not sub.startswith((" ", "\t")):
                    break
                if not sub_stripped.startswith("- "):
                    i += 1
                    continue
                entry: dict[str, Any] = {}
                first_kv = sub_stripped[2:]
                if ":" in first_kv:
                    k, _, v = first_kv.partition(":")
                    entry[k.strip()] = _unquote(_strip_inline_comment(v).strip())
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    nxt_stripped = nxt.strip()
                    if not nxt_stripped or nxt_stripped.startswith("#"):
                        i += 1
                        continue
                    if not nxt.startswith((" ", "\t")):
                        break
                    if nxt_stripped.startswith("- "):
                        break
                    if ":" in nxt_stripped:
                        k, _, v = nxt_stripped.partition(":")
                        entry[k.strip()] = _unquote(_strip_inline_comment(v).strip())
                    i += 1
                items.append(entry)
            out[key] = items
            continue
        if value.lower() in ("null", "~"):
            out[key] = None
        else:
            out[key] = _unquote(value)
        i += 1
    return out


def test_phase_2_stories_blocked_until_decision_file_present() -> None:
    """No 2.x story may be ``done`` without a fresh decision.

    Walks ``phase-2.md`` for stories whose ``Status:`` line is ``done``; if any
    exist, the decision file must exist with ``outcome in {GO, PIVOT}``. The
    error message names the offending story so reviewers see the gate fire
    concretely in CI.
    """
    assert PHASE_2_PATH.is_file(), (
        f"Expected Phase 2 plan at {PHASE_2_PATH.relative_to(REPO_ROOT)}; "
        "the calibration gate has nothing to walk against without it."
    )

    stories = _walk_phase_2_stories(PHASE_2_PATH.read_text())
    gating_stories = [
        (story_id, title, status)
        for story_id, title, status in stories
        if status in GATING_STATUSES
    ]

    if not gating_stories:
        return  # Gate is dormant; no Phase 2 work has tried to ship.

    offending = ", ".join(
        f"{story_id} ({title}, status={status})"
        for story_id, title, status in gating_stories
    )

    assert DECISION_PATH.is_file(), (
        f"Phase 2 calibration gate tripped: {offending} is done, but "
        f"{DECISION_PATH.relative_to(REPO_ROOT)} does not exist. "
        "Per phase-1.5.md US-003, no 2.x story may ship until the decision "
        "file is committed with outcome in {GO, PIVOT}."
    )

    frontmatter = _parse_decision_frontmatter(DECISION_PATH.read_text())
    outcome_raw = frontmatter.get("outcome", "")
    outcome = outcome_raw.strip() if isinstance(outcome_raw, str) else ""

    assert outcome in ADMITTED_OUTCOMES, (
        f"Phase 2 calibration gate tripped: {offending} requires "
        f"{DECISION_PATH.relative_to(REPO_ROOT)} to declare "
        f"outcome in {sorted(ADMITTED_OUTCOMES)}, got outcome={outcome!r}. "
        "Decisions of KILL or missing/invalid outcome do not admit Phase 2 work."
    )


def test_decision_file_schema_when_present() -> None:
    """When ``phase-1.5-decision.md`` exists, its frontmatter must be well-formed.

    Asserts:
      - the frontmatter parses;
      - required keys are present and well-typed;
      - ``decision_date`` is an ISO date within the last 365 days;
      - for ``outcome: PIVOT``, ``phase_2_scope_changes`` is a non-empty list
        and each entry has ``story`` and ``change``.

    If the decision file is absent, the test is dormant — admission is the
    job of ``test_phase_2_stories_blocked_until_decision_file_present``.
    """
    if not DECISION_PATH.is_file():
        return

    rel = DECISION_PATH.relative_to(REPO_ROOT)
    text = DECISION_PATH.read_text()
    frontmatter = _parse_decision_frontmatter(text)

    assert frontmatter, (
        f"{rel} exists but has no parseable `---` frontmatter block. "
        "Copy solana-plans/phase-1.5-decision.md.template and fill in the keys."
    )

    outcome = frontmatter.get("outcome")
    assert isinstance(outcome, str) and outcome in VALID_OUTCOMES, (
        f"{rel}: `outcome` must be one of {sorted(VALID_OUTCOMES)}, "
        f"got {outcome!r}."
    )

    decision_date_raw = frontmatter.get("decision_date")
    assert isinstance(decision_date_raw, str) and decision_date_raw, (
        f"{rel}: `decision_date` is required and must be an ISO date "
        f"(YYYY-MM-DD), got {decision_date_raw!r}."
    )
    try:
        decision_date = datetime.strptime(decision_date_raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise AssertionError(
            f"{rel}: `decision_date` must be a YYYY-MM-DD ISO date, "
            f"got {decision_date_raw!r} ({exc})."
        ) from None

    age = date.today() - decision_date
    assert timedelta(days=0) <= age <= timedelta(days=DECISION_STALENESS_DAYS), (
        f"{rel}: `decision_date` is {decision_date.isoformat()} "
        f"({age.days} days old); must be within the last "
        f"{DECISION_STALENESS_DAYS} days. Stale decisions must be re-validated "
        "before Phase 2 work resumes."
    )

    assert "phase_2_scope_changes" in frontmatter, (
        f"{rel}: `phase_2_scope_changes` is required (set to `null` for "
        "GO/KILL outcomes; non-empty list for PIVOT)."
    )
    scope_changes = frontmatter["phase_2_scope_changes"]

    if outcome == "PIVOT":
        assert isinstance(scope_changes, list) and scope_changes, (
            f"{rel}: outcome=PIVOT requires `phase_2_scope_changes` to be a "
            f"non-empty list, got {scope_changes!r}."
        )
        for idx, entry in enumerate(scope_changes):
            assert isinstance(entry, dict), (
                f"{rel}: phase_2_scope_changes[{idx}] must be a mapping with "
                f"`story` and `change` keys, got {entry!r}."
            )
            story = entry.get("story")
            change = entry.get("change")
            assert isinstance(story, str) and story, (
                f"{rel}: phase_2_scope_changes[{idx}] missing or empty `story`."
            )
            assert isinstance(change, str) and change, (
                f"{rel}: phase_2_scope_changes[{idx}] missing or empty `change`."
            )
    else:
        assert scope_changes is None, (
            f"{rel}: outcome={outcome} requires `phase_2_scope_changes: null`, "
            f"got {scope_changes!r}. Scope changes are PIVOT-only."
        )


def test_calibration_data_decision_present_when_phase_2_done() -> None:
    """The calibration data gate (Gate B) must be signed off before 2.x ships.

    Mirror of ``test_phase_2_stories_blocked_until_decision_file_present`` for
    the *other* Phase 2 gate precondition: ``phase-2.md`` Gate B requires
    ``solana-plans/phase-2-entry-gate.md`` to exist with frontmatter naming the
    data ``provider`` and ``corpus_scope``. Without it, no 2.x story may be
    marked ``done``.
    """
    assert PHASE_2_PATH.is_file(), (
        f"Expected Phase 2 plan at {PHASE_2_PATH.relative_to(REPO_ROOT)}; "
        "the calibration gate has nothing to walk against without it."
    )

    stories = _walk_phase_2_stories(PHASE_2_PATH.read_text())
    gating_stories = [
        (story_id, title, status)
        for story_id, title, status in stories
        if status in GATING_STATUSES
    ]

    if not gating_stories:
        return  # Gate is dormant; no Phase 2 work has tried to ship.

    offending = ", ".join(
        f"{story_id} ({title}, status={status})"
        for story_id, title, status in gating_stories
    )

    assert CALIBRATION_GATE_PATH.is_file(), (
        f"Phase 2 calibration gate tripped: {offending} is done, but "
        f"{CALIBRATION_GATE_PATH.relative_to(REPO_ROOT)} does not exist. "
        "Per phase-2.md Gate B, no 2.x story may ship until the calibration "
        "data decision is signed off (provider and corpus_scope)."
    )

    rel = CALIBRATION_GATE_PATH.relative_to(REPO_ROOT)
    frontmatter = _parse_decision_frontmatter(CALIBRATION_GATE_PATH.read_text())
    assert frontmatter, (
        f"{rel} exists but has no parseable `---` frontmatter block. "
        "Required keys per phase-2.md Gate B: "
        f"{', '.join(CALIBRATION_GATE_REQUIRED_KEYS)}."
    )

    missing = [
        key
        for key in CALIBRATION_GATE_REQUIRED_KEYS
        if not (isinstance(frontmatter.get(key), str) and frontmatter[key].strip())
    ]
    assert not missing, (
        f"Phase 2 calibration gate tripped: {offending} requires {rel} frontmatter "
        f"to declare {sorted(CALIBRATION_GATE_REQUIRED_KEYS)}; missing or empty: "
        f"{sorted(missing)}."
    )


def _pivot_scope_changes_violation(frontmatter: dict[str, Any]) -> str | None:
    """Return a violation message if a PIVOT frontmatter has a bad scope_changes shape.

    Returns ``None`` when ``frontmatter`` is a well-formed PIVOT decision (or
    when ``outcome`` is not PIVOT — this helper does not enforce non-PIVOT
    rules). Captures the rule used by
    ``test_decision_file_schema_when_present`` so the focused test below can
    exercise it against synthetic fixtures without touching the real file.
    """
    if frontmatter.get("outcome") != "PIVOT":
        return None
    if "phase_2_scope_changes" not in frontmatter:
        return "phase_2_scope_changes key missing"
    scope_changes = frontmatter["phase_2_scope_changes"]
    if not isinstance(scope_changes, list) or not scope_changes:
        return f"phase_2_scope_changes must be a non-empty list, got {scope_changes!r}"
    for idx, entry in enumerate(scope_changes):
        if not isinstance(entry, dict):
            return f"phase_2_scope_changes[{idx}] must be a mapping, got {entry!r}"
        story = entry.get("story")
        change = entry.get("change")
        if not (isinstance(story, str) and story):
            return f"phase_2_scope_changes[{idx}] missing or empty `story`"
        if not (isinstance(change, str) and change):
            return f"phase_2_scope_changes[{idx}] missing or empty `change`"
    return None


def test_pivot_outcome_requires_non_empty_scope_changes() -> None:
    """A PIVOT decision must declare ``phase_2_scope_changes`` as a non-empty list.

    Synthetic-fixture test exercising the PIVOT
    scope-changes rule directly against in-memory frontmatter strings,
    independent of whether the real decision file exists. Each rejection
    case must trip ``_pivot_scope_changes_violation``; the acceptance case
    must pass it.
    """
    today = date.today().isoformat()

    rejected_cases: list[tuple[str, str]] = [
        (
            "scope_changes set to null",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n"
            "phase_2_scope_changes: null\n---\n",
        ),
        (
            "scope_changes key missing entirely",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n---\n",
        ),
        (
            "scope_changes block sequence with no items",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n"
            "phase_2_scope_changes:\n---\n",
        ),
        (
            "entry missing change",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n"
            "phase_2_scope_changes:\n  - story: \"2.3\"\n---\n",
        ),
        (
            "entry missing story",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n"
            "phase_2_scope_changes:\n  - change: \"reduce scope\"\n---\n",
        ),
    ]
    for label, raw in rejected_cases:
        frontmatter = _parse_decision_frontmatter(raw)
        violation = _pivot_scope_changes_violation(frontmatter)
        assert violation is not None, (
            f"PIVOT case `{label}` should have failed schema validation "
            f"but did not. Parsed frontmatter: {frontmatter!r}."
        )

    accepted = (
        f"---\noutcome: PIVOT\ndecision_date: {today}\n"
        "phase_2_scope_changes:\n"
        "  - story: \"2.3\"\n"
        "    change: \"narrow Whirlpool scope to xy=k stand-in\"\n"
        "  - story: \"2.6\"\n"
        "    change: \"defer wallet integration to phase 3\"\n"
        "---\n"
    )
    frontmatter = _parse_decision_frontmatter(accepted)
    violation = _pivot_scope_changes_violation(frontmatter)
    assert violation is None, (
        f"Well-formed PIVOT frontmatter was rejected: {violation!r}. "
        f"Parsed: {frontmatter!r}."
    )


def _kill_outcome_admission_violation(frontmatter: dict[str, Any]) -> str | None:
    """Return a violation message if ``outcome`` does not admit Phase 2 work.

    Mirrors the second ``assert`` in
    ``test_phase_2_stories_blocked_until_decision_file_present``:
    ``outcome`` must be in ``ADMITTED_OUTCOMES`` ({GO, PIVOT}). KILL,
    missing, empty, or any non-admitted value blocks admission.
    """
    outcome_raw = frontmatter.get("outcome")
    outcome = outcome_raw.strip() if isinstance(outcome_raw, str) else ""
    if outcome in ADMITTED_OUTCOMES:
        return None
    return (
        f"outcome={outcome_raw!r} does not admit Phase 2 work; "
        f"required one of {sorted(ADMITTED_OUTCOMES)}"
    )


def test_kill_outcome_blocks_phase_2() -> None:
    """A KILL decision must not admit completed Phase 2 stories.

    Synthetic-fixture test exercising the admission
    rule directly: ``outcome: KILL`` is a *valid* schema value (KILL is in
    ``VALID_OUTCOMES``) but is *not* admitted (KILL is not in
    ``ADMITTED_OUTCOMES``). The schema test accepts a KILL decision; the
    admission gate rejects it.
    """
    today = date.today().isoformat()

    rejected_cases: list[tuple[str, str]] = [
        (
            "outcome KILL",
            f"---\noutcome: KILL\ndecision_date: {today}\n"
            "phase_2_scope_changes: null\n---\n",
        ),
        (
            "outcome key missing",
            f"---\ndecision_date: {today}\nphase_2_scope_changes: null\n---\n",
        ),
        (
            "outcome empty string",
            f'---\noutcome: ""\ndecision_date: {today}\n'
            "phase_2_scope_changes: null\n---\n",
        ),
        (
            "outcome unknown value",
            f"---\noutcome: MAYBE\ndecision_date: {today}\n"
            "phase_2_scope_changes: null\n---\n",
        ),
        (
            "outcome lowercase go (case-sensitive)",
            f"---\noutcome: go\ndecision_date: {today}\n"
            "phase_2_scope_changes: null\n---\n",
        ),
    ]
    for label, raw in rejected_cases:
        frontmatter = _parse_decision_frontmatter(raw)
        violation = _kill_outcome_admission_violation(frontmatter)
        assert violation is not None, (
            f"Admission case `{label}` should have been blocked but was "
            f"admitted. Parsed frontmatter: {frontmatter!r}."
        )

    accepted_cases: list[tuple[str, str]] = [
        (
            "outcome GO",
            f"---\noutcome: GO\ndecision_date: {today}\n"
            "phase_2_scope_changes: null\n---\n",
        ),
        (
            "outcome PIVOT",
            f"---\noutcome: PIVOT\ndecision_date: {today}\n"
            "phase_2_scope_changes:\n"
            '  - story: "2.3"\n'
            '    change: "narrow Whirlpool scope"\n---\n',
        ),
    ]
    for label, raw in accepted_cases:
        frontmatter = _parse_decision_frontmatter(raw)
        violation = _kill_outcome_admission_violation(frontmatter)
        assert violation is None, (
            f"Admission case `{label}` should have been admitted but was "
            f"blocked: {violation!r}. Parsed: {frontmatter!r}."
        )


def _decision_staleness_violation(
    frontmatter: dict[str, Any], today: date
) -> str | None:
    """Return a violation message if ``decision_date`` is missing, malformed, or stale.

    Mirrors the ``decision_date`` block in
    ``test_decision_file_schema_when_present``:
      - ``decision_date`` must be a non-empty string;
      - it must parse as a YYYY-MM-DD ISO date;
      - the age relative to ``today`` must satisfy
        ``0 <= age.days <= DECISION_STALENESS_DAYS``.

    ``today`` is injected so boundary cases are testable without freezegun.
    """
    raw = frontmatter.get("decision_date")
    if not (isinstance(raw, str) and raw):
        return f"decision_date missing or empty, got {raw!r}"
    try:
        decision_date = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        return f"decision_date {raw!r} is not a YYYY-MM-DD ISO date ({exc})"
    age = today - decision_date
    if age < timedelta(days=0):
        return (
            f"decision_date {decision_date.isoformat()} is in the future "
            f"(age={age.days} days)"
        )
    if age > timedelta(days=DECISION_STALENESS_DAYS):
        return (
            f"decision_date {decision_date.isoformat()} is "
            f"{age.days} days old; must be within the last "
            f"{DECISION_STALENESS_DAYS} days"
        )
    return None


def test_stale_decision_blocks_phase_2() -> None:
    """A stale (>365 days old) or future-dated decision must not admit Phase 2 work.

    Synthetic-fixture test exercising the
    ``decision_date`` freshness rule directly. Boundary is inclusive: a
    decision exactly 365 days old still admits; 366 days old does not.
    Future-dated decisions are also rejected (the schema test's
    ``0 <= age`` clause).
    """
    today = date(2026, 4, 28)

    def fixture(decision_date: str) -> str:
        return (
            f"---\noutcome: GO\ndecision_date: {decision_date}\n"
            "phase_2_scope_changes: null\n---\n"
        )

    rejected_cases: list[tuple[str, str]] = [
        (
            "366 days old (one past the boundary)",
            fixture((today - timedelta(days=366)).isoformat()),
        ),
        (
            "1 day in the future",
            fixture((today + timedelta(days=1)).isoformat()),
        ),
        (
            "decision_date missing entirely",
            "---\noutcome: GO\nphase_2_scope_changes: null\n---\n",
        ),
        (
            "decision_date malformed (not ISO)",
            fixture("April 28, 2026"),
        ),
    ]
    for label, raw in rejected_cases:
        frontmatter = _parse_decision_frontmatter(raw)
        violation = _decision_staleness_violation(frontmatter, today)
        assert violation is not None, (
            f"Staleness case `{label}` should have been blocked but was "
            f"admitted. Parsed frontmatter: {frontmatter!r}."
        )

    accepted_cases: list[tuple[str, str]] = [
        ("today", fixture(today.isoformat())),
        (
            "exactly 365 days old (boundary inclusive)",
            fixture((today - timedelta(days=DECISION_STALENESS_DAYS)).isoformat()),
        ),
    ]
    for label, raw in accepted_cases:
        frontmatter = _parse_decision_frontmatter(raw)
        violation = _decision_staleness_violation(frontmatter, today)
        assert violation is None, (
            f"Staleness case `{label}` should have been admitted but was "
            f"blocked: {violation!r}. Parsed: {frontmatter!r}."
        )
