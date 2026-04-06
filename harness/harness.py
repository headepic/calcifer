#!/usr/bin/env python3
"""Calcifer harness CLI.

Subcommands:
    status          Show feature backlog summary
    pick            Pick the highest-priority pending feature
    verify <id>     Run verification commands for a feature
    complete <id>   Mark a feature as passing (after verify succeeds)
    add <id>        Add a new feature to the backlog (scaffolds contract)

All state lives in harness/features.json. This script is the ONLY thing that
should modify the passes field.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
ROOT = HARNESS_DIR.parent
FEATURES_FILE = HARNESS_DIR / "features.json"
CONTRACTS_DIR = HARNESS_DIR / "contracts"
PROGRESS_FILE = HARNESS_DIR / "progress.md"

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Per-command timeout for verification
VERIFY_TIMEOUT_S = 600

# Allow-listed command prefixes for verification
# Each entry is a shlex-split prefix that the start of cmd tokens must match.
VERIFY_ALLOWLIST = [
    ["grep"],
    ["pytest"],
    [".venv/bin/python"],
    [".venv/bin/pytest"],
    ["python", "-c"],
    ["python", "-m", "pytest"],
    ["python3", "-c"],
    ["python3", "-m", "pytest"],
]

# Placeholder verification command written by `cmd_add` — must be replaced
# before a feature is pickable. Acts as a sentinel so the harness can
# distinguish "plan in progress" from "plan done, ready to implement".
PLACEHOLDER_VERIFY = ".venv/bin/python -c \"import calcifer; assert False, 'not implemented'\""


@dataclass
class Feature:
    id: str
    title: str
    category: str
    priority: str
    description: str
    motivation: str
    acceptance_criteria: list[str]
    verification: list[str]
    reference: str
    status: str  # "pending", "in_progress", "blocked", "done"
    passes: bool
    # Verify cache — populated by cmd_verify on success, consumed by cmd_complete
    verified_sha: str = ""       # HEAD SHA at verify time
    verified_tree: str = ""      # Working tree fingerprint at verify time (excludes harness/)
    blocked_reason: str = ""
    # Contract review cache — populated by cmd_review_record, gates cmd_verify
    review_status: str = ""             # "", "approved", "changes_requested", "blocking"
    review_notes: str = ""              # reviewer feedback
    reviewed_at: str = ""               # ISO 8601 UTC timestamp
    reviewed_contract_sha: str = ""     # sha256[:16] of contract file bytes at review time
    reviewer: str = ""                  # "self", "subagent", "human", "external" — MUST NOT be "self" for non-bootstrap features

    @property
    def phase(self) -> str:
        """Derive the plan→generate→verify→done phase from other fields.

        Not stored — computed from passes, status, review_status, and
        whether the verification is still the placeholder. This keeps the
        state machine's single source of truth in the fields that already
        gate behavior, and avoids drift.

        Phases:
          plan_stub      — cmd_add placeholder verification still present
          plan_drafting  — contract exists but not yet submitted for review
          plan_review    — review_status='changes_requested' (needs revision)
          generating     — review approved, verify not yet run or not cached
          verifying      — verify passed (cache present) but not yet complete
          done           — passes=True
          blocked        — status='blocked'
        """
        if self.passes:
            return "done"
        if self.status == "blocked":
            return "blocked"
        if any(cmd.strip() == PLACEHOLDER_VERIFY for cmd in self.verification):
            return "plan_stub"
        if self.review_status == "changes_requested":
            return "plan_review"
        if self.review_status != "approved":
            return "plan_drafting"
        # review approved — check verify cache
        if self.verified_sha and self.verified_tree:
            return "verifying"
        return "generating"

    @classmethod
    def from_dict(cls, d: dict) -> "Feature":
        return cls(
            id=d["id"],
            title=d["title"],
            category=d["category"],
            priority=d.get("priority", "medium"),
            description=d.get("description", ""),
            motivation=d.get("motivation", ""),
            acceptance_criteria=d.get("acceptance_criteria", []),
            verification=d.get("verification", []),
            reference=d.get("reference", ""),
            status=d.get("status", "pending"),
            passes=d.get("passes", False),
            verified_sha=d.get("verified_sha", ""),
            verified_tree=d.get("verified_tree", ""),
            blocked_reason=d.get("blocked_reason", ""),
            review_status=d.get("review_status", ""),
            review_notes=d.get("review_notes", ""),
            reviewed_at=d.get("reviewed_at", ""),
            reviewed_contract_sha=d.get("reviewed_contract_sha", ""),
            reviewer=d.get("reviewer", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "priority": self.priority,
            "description": self.description,
            "motivation": self.motivation,
            "acceptance_criteria": self.acceptance_criteria,
            "verification": self.verification,
            "reference": self.reference,
            "status": self.status,
            "passes": self.passes,
            "verified_sha": self.verified_sha,
            "verified_tree": self.verified_tree,
            "blocked_reason": self.blocked_reason,
            "review_status": self.review_status,
            "review_notes": self.review_notes,
            "reviewed_at": self.reviewed_at,
            "reviewed_contract_sha": self.reviewed_contract_sha,
            "reviewer": self.reviewer,
        }


def load_features() -> tuple[dict, list[Feature]]:
    """Return (raw_data, features_list)."""
    if not FEATURES_FILE.exists():
        return {"version": 1, "features": []}, []
    with FEATURES_FILE.open() as f:
        data = json.load(f)
    features = [Feature.from_dict(d) for d in data.get("features", [])]
    return data, features


def save_features(data: dict) -> None:
    """Atomic save: write to temp file in same dir, then os.replace."""
    FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".features.", suffix=".json.tmp", dir=str(FEATURES_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, FEATURES_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def git_head_sha() -> str:
    """Return current HEAD short SHA, or empty string if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def progress_last_modified_sha() -> str:
    """Return the commit SHA that last modified progress.md, or empty."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(PROGRESS_FILE.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# Paths that are allowed to be dirty during verify/complete without invalidating the cache
_HARNESS_DIRTY_ALLOWLIST = {"harness/features.json", "harness/progress.md"}


def working_tree_fingerprint() -> str:
    """Hash of the current working tree state relative to HEAD.

    Combines:
      - `git diff HEAD` for tracked files (staged + unstaged)
      - For each untracked non-ignored file: path + SHA256 of its contents

    Excludes harness/features.json and harness/progress.md (expected to be
    modified during the harness workflow itself).

    Hashing untracked *contents* (not just names) closes a narrow bypass
    where a user could verify against an untracked file, then swap its
    body with a dummy while keeping the filename. Returns empty string on
    error.
    """
    import hashlib
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--"] + [
                f":(exclude){p}" for p in _HARNESS_DIRTY_ALLOWLIST
            ],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout

        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout

        # Hash each untracked file's contents (bounded read: 10 MB/file cap)
        untracked_parts: list[str] = []
        for rel_path in sorted(untracked.splitlines()):
            if rel_path in _HARNESS_DIRTY_ALLOWLIST:
                continue
            abs_path = ROOT / rel_path
            try:
                if abs_path.is_file():
                    size = abs_path.stat().st_size
                    if size > 10 * 1024 * 1024:
                        # Too large to hash; include size in the fingerprint instead
                        untracked_parts.append(f"{rel_path}\0LARGE:{size}")
                    else:
                        content = abs_path.read_bytes()
                        digest = hashlib.sha256(content).hexdigest()
                        untracked_parts.append(f"{rel_path}\0{digest}")
                else:
                    # Symlink, fifo, etc. — just include the path
                    untracked_parts.append(f"{rel_path}\0NONFILE")
            except OSError as e:
                untracked_parts.append(f"{rel_path}\0ERR:{e}")

        combined = diff + "\n---UNTRACKED---\n" + "\n".join(untracked_parts)
        return hashlib.sha256(combined.encode("utf-8", errors="replace")).hexdigest()[:16]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# ---- Contract review helpers (harness-contract-review) ----

def _contract_sha(feature_id: str) -> str:
    """Return sha256[:16] of a contract file, or '' if missing."""
    import hashlib
    p = contract_path(feature_id)
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _extract_referenced_paths(text: str) -> list[str]:
    """Heuristic: pull file-like paths out of contract text.

    Matches tokens that look like `foo/bar.py`, `calcifer-source/src/x.ts`,
    or claude-code-source/... references. Used only to check existence
    for the review packet's machine sanity section — not exhaustive.
    """
    # Match things like `calcifer/foo/bar.py`, `tests/test_x.py`, `src/foo.ts`
    # Allow .py .ts .md .json .toml .sh suffixes. Must contain at least one /.
    pattern = re.compile(
        r"(?<![A-Za-z0-9_/.-])"
        r"([A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|ts|md|json|toml|sh|yml|yaml))"
        r"(?![A-Za-z0-9_/.-])"
    )
    seen: set[str] = set()
    out: list[str] = []
    for m in pattern.finditer(text):
        path = m.group(1)
        # Strip trailing punctuation (parens, colons, commas)
        path = path.rstrip(".,;:)")
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _required_contract_sections() -> list[str]:
    """Section headings that must exist (non-empty) in every contract."""
    return [
        "## Motivation",
        "## Claude Code Reference",
        "## Scope",
        "## Design",
        "## Acceptance Criteria",
        "## Verification Commands",
    ]


def _machine_sanity(feature: Feature) -> list[tuple[str, str]]:
    """Return a list of (status, message) tuples for the review packet.

    Statuses: "OK", "WARN", "FAIL". No subprocess calls (fast, local).
    """
    results: list[tuple[str, str]] = []

    # 1. Contract file exists
    contract = contract_path(feature.id)
    if not contract.exists():
        results.append(("FAIL", f"Contract file missing: {contract}"))
        return results  # Nothing else worth checking
    contract_text = contract.read_text(encoding="utf-8", errors="replace")
    results.append(("OK", f"Contract file exists ({len(contract_text)} chars)"))

    # 2. Required sections present and non-empty
    for section in _required_contract_sections():
        idx = contract_text.find(section)
        if idx < 0:
            results.append(("FAIL", f"Missing section: {section}"))
            continue
        # Find the end of this section (next ## heading or EOF)
        body_start = idx + len(section)
        next_section = contract_text.find("\n## ", body_start)
        body_end = next_section if next_section >= 0 else len(contract_text)
        body = contract_text[body_start:body_end].strip()
        if not body or len(body) < 20:
            results.append(("WARN", f"Section is suspiciously short: {section}"))
        else:
            results.append(("OK", f"Section present: {section}"))

    # 3. No TODO markers left (ignore backtick-quoted references to the string)
    todo_lines: list[int] = []
    for i, line in enumerate(contract_text.splitlines(), 1):
        if "TODO:" not in line:
            continue
        # Strip backtick-quoted spans, then check again. This way a line
        # like `literal \`TODO:\` marker` (which discusses the concept)
        # doesn't trigger — but a real unquoted `TODO: fill in` does.
        stripped = re.sub(r"`[^`]*`", "", line)
        if "TODO:" in stripped:
            todo_lines.append(i)
    if todo_lines:
        results.append((
            "FAIL",
            f"Literal 'TODO:' placeholder(s) on line(s): {todo_lines[:5]}",
        ))
    else:
        results.append(("OK", "No TODO: placeholder markers"))

    # 4. features.json verification commands validate
    if not feature.verification:
        results.append(("FAIL", "features.json verification list is empty"))
    else:
        for i, cmd in enumerate(feature.verification, 1):
            _, err = validate_and_parse_verify_command(cmd)
            if err is not None:
                results.append(("FAIL", f"Verification cmd {i} invalid: {err}"))
            else:
                results.append(("OK", f"Verification cmd {i} validates"))

    # 5. Referenced file paths exist (for the ones that look like repo-relative)
    paths = _extract_referenced_paths(contract_text)
    for path in paths:
        abs_path = ROOT / path
        # Claude Code source references are absolute-ish — skip those
        if path.startswith("claude-code-source/") or "claude-code-source" in path:
            continue
        if abs_path.exists():
            results.append(("OK", f"Referenced file exists: {path}"))
        else:
            # Could be a new file the feature will create — warn, don't fail
            results.append(("WARN", f"Referenced file not found (new?): {path}"))

    # 6. Contract ↔ features.json verification command sync (Rec 12)
    # Extract the commands listed in the contract's "## Verification Commands"
    # fenced code block and compare to feature.verification. They must match.
    contract_cmds = _extract_contract_verification_commands(contract_text)
    if contract_cmds is not None:
        # Normalize: strip, drop empties
        norm_contract = [c.strip() for c in contract_cmds if c.strip()]
        norm_features = [c.strip() for c in feature.verification if c.strip()]
        if norm_contract != norm_features:
            results.append((
                "FAIL",
                f"Contract verification commands ({len(norm_contract)}) do not "
                f"match features.json ({len(norm_features)}). Drift between "
                "the two canonical sources. Sync them before approving.",
            ))
        else:
            results.append(("OK", f"Contract/features.json verification in sync ({len(norm_contract)} commands)"))
    else:
        results.append(("WARN", "Could not parse '## Verification Commands' fenced block from contract"))

    # 7. Planning-stub detection (Rec 7)
    if any(cmd.strip() == PLACEHOLDER_VERIFY for cmd in feature.verification):
        results.append((
            "FAIL",
            "features.json still contains the cmd_add placeholder verify — "
            "contract has not been filled in. This is planning debt, not a "
            "pickable feature.",
        ))

    return results


def _extract_contract_verification_commands(contract_text: str) -> list[str] | None:
    """Parse the commands inside the '## Verification Commands' fenced block.

    Returns the list of commands, or None if the section / fence is missing.
    """
    # Find the ## Verification Commands heading
    idx = contract_text.find("\n## Verification Commands")
    if idx < 0:
        # Try without leading newline (start of file — rare)
        if contract_text.startswith("## Verification Commands"):
            idx = 0
        else:
            return None
    # Find the next fenced code block after this heading
    body_start = idx + len("\n## Verification Commands")
    next_section = contract_text.find("\n## ", body_start)
    body = contract_text[body_start:next_section if next_section >= 0 else len(contract_text)]

    # Find ``` fence
    m = re.search(r"\n```(?:\w*)?\n(.*?)\n```", body, re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    # Each non-empty line is one command
    return [ln for ln in block.splitlines() if ln.strip()]


def _reviewer_checklist_path() -> Path:
    """Resolve the checklist path lazily so tests can monkeypatch HARNESS_DIR."""
    return HARNESS_DIR / "reviewer-checklist.md"


def _load_reviewer_checklist() -> str:
    """Read the checklist file. Fall back to a minimal default if missing."""
    path = _reviewer_checklist_path()
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "(reviewer-checklist.md missing — using minimal default)\n"
        "1. Does the contract accurately describe the problem?\n"
        "2. Do verification commands fail before implementation?\n"
        "3. Are acceptance criteria yes/no verifiable?\n"
    )


def _render_review_packet(feature: Feature) -> str:
    """Build the human-readable review packet text."""
    contract = contract_path(feature.id)
    sha = _contract_sha(feature.id)

    lines: list[str] = []
    lines.append(f"===== REVIEW PACKET: {feature.id} =====")
    lines.append("")
    lines.append("[METADATA]")
    lines.append(f"  title:    {feature.title}")
    lines.append(f"  category: {feature.category}")
    lines.append(f"  priority: {feature.priority}")
    lines.append(f"  status:   {feature.status}")
    lines.append(f"  passes:   {feature.passes}")
    if feature.review_status:
        lines.append(f"  prior review: {feature.review_status} at {feature.reviewed_at}")
        if feature.reviewed_contract_sha != sha:
            lines.append(f"  WARN: contract sha ({sha}) != reviewed sha ({feature.reviewed_contract_sha}) — prior review invalidated")
    lines.append("")

    lines.append("[CONTRACT FILE]")
    lines.append(f"  path: {contract.relative_to(ROOT) if contract.exists() else contract}")
    lines.append(f"  sha:  {sha or '(missing)'}")
    lines.append("")

    lines.append("[MACHINE SANITY]")
    for status, msg in _machine_sanity(feature):
        marker = {"OK": " OK ", "WARN": "WARN", "FAIL": "FAIL"}[status]
        lines.append(f"  {marker}  {msg}")
    lines.append("")

    lines.append("[FEATURES.JSON ENTRY]")
    entry_json = json.dumps(feature.to_dict(), indent=2, ensure_ascii=False)
    for ln in entry_json.splitlines():
        lines.append(f"  {ln}")
    lines.append("")

    lines.append("[CONTRACT CONTENT]")
    if contract.exists():
        text = contract.read_text(encoding="utf-8", errors="replace")
        for ln in text.splitlines():
            lines.append(f"  {ln}")
    else:
        lines.append("  (contract file does not exist)")
    lines.append("")

    lines.append("[REVIEWER CHECKLIST]")
    _cl_path = _reviewer_checklist_path()
    lines.append(f"  (loaded from {_cl_path.relative_to(ROOT) if _cl_path.exists() else '<default>'})")
    for ln in _load_reviewer_checklist().splitlines():
        lines.append(f"  {ln}")
    lines.append("")

    # Prior review history (Rec 3: the calibration corpus)
    history_file = _reviews_dir() / f"{feature.id}.jsonl"
    if history_file.exists():
        lines.append("[REVIEW HISTORY]")
        for ln in history_file.read_text(encoding="utf-8").strip().splitlines():
            try:
                entry = json.loads(ln)
                lines.append(f"  {entry.get('ts', '?')[:19]} {entry.get('status') or entry.get('event', '?'):22} "
                            f"reviewer={entry.get('reviewer', '-'):10} sha={entry.get('contract_sha', '-')}")
                if entry.get('notes'):
                    lines.append(f"    notes: {entry['notes'][:150]}")
                if entry.get('what'):
                    lines.append(f"    MISS: {entry['what'][:150]}")
            except json.JSONDecodeError:
                continue
        lines.append("")

    lines.append("[HOW TO RECORD YOUR VERDICT]")
    lines.append(f"  python harness/harness.py review-record {feature.id} \\")
    lines.append(f"    --reviewer {{self|subagent|human|external}} \\")
    lines.append(f"    --status {{approved|changes_requested|blocking}} \\")
    lines.append(f"    --notes 'specific feedback, cite line numbers'")
    lines.append("")
    lines.append("  reviewer=self is REJECTED except for bootstrap features.")
    lines.append("  Use --reviewer subagent and invoke from a FRESH-CONTEXT Agent tool call.")
    lines.append("")
    lines.append("  approved:          ready to implement")
    lines.append("  changes_requested: issues must be fixed; re-submit for review")
    lines.append("  blocking:          fundamental problem (wrong scope, already done, infeasible)")
    lines.append("")
    lines.append("===== END REVIEW PACKET =====")

    return "\n".join(lines)


def validate_and_parse_verify_command(cmd: str) -> tuple[list[str] | None, str | None]:
    """Tokenize a verify command and check it matches the allow-list.

    Returns (argv, None) if OK, or (None, error_message) if rejected.

    Verification commands are executed WITHOUT a shell (subprocess.run
    with a list argv, not shell=True). This already eliminates all
    shell-injection classes (newlines, redirects, command substitution,
    glob expansion, process substitution, etc.) — the shell never sees
    the string.

    This function adds defense-in-depth:
      1. Reject tokens containing control characters (newlines, tabs as
         whole tokens) so bugs in callers that build commands can't smuggle
         multi-line payloads past the logging.
      2. Reject tokens that look like unquoted redirects (> < | & ;) —
         these are harmless without a shell but signal the author intended
         a shell command and should rewrite it.
      3. Enforce the allow-list prefix.
      4. Reject the `cmd_add` placeholder sentinel so a stub feature
         can't accidentally ship.
    """
    # Reject the planning placeholder verbatim
    if cmd.strip() == PLACEHOLDER_VERIFY:
        return None, (
            "rejected: verification is still the cmd_add placeholder. "
            "Fill in real import/attribute checks before trying to verify."
        )

    if "\n" in cmd or "\r" in cmd:
        return None, "rejected: command contains newline"

    try:
        tokens = shlex.split(cmd)
    except ValueError as e:
        return None, f"rejected: unparseable ({e})"

    if not tokens:
        return None, "rejected: empty command"

    # Reject tokens that look like unquoted shell syntax (even though harmless
    # without shell=True, they're a code smell and the author likely meant
    # something shell-interpreted)
    for tok in tokens:
        stripped = tok.lstrip(">").lstrip("<").lstrip("|").lstrip("&")
        if not stripped and tok:
            return None, f"rejected: token {tok!r} looks like unquoted shell redirect/pipe"
        if tok.startswith(">") or tok.startswith("<") or tok.startswith("|"):
            return None, f"rejected: token {tok!r} starts with shell redirect char"

    for prefix in VERIFY_ALLOWLIST:
        if len(tokens) >= len(prefix) and tokens[: len(prefix)] == prefix:
            return tokens, None

    return None, f"rejected: command prefix not in allow-list (got {tokens[:2]!r})"


def find_feature(features: list[Feature], feature_id: str) -> Feature | None:
    for f in features:
        if f.id == feature_id:
            return f
    return None


def contract_path(feature_id: str) -> Path:
    return CONTRACTS_DIR / f"{feature_id}.md"


# -- Commands --


def cmd_status(args: argparse.Namespace) -> int:
    data, features = load_features()
    if not features:
        print("No features in backlog. Add one with: harness.py add <id>")
        return 0

    # Phase counts (from derived Feature.phase property)
    phase_counts: dict[str, int] = {}
    for f in features:
        phase_counts[f.phase] = phase_counts.get(f.phase, 0) + 1

    print(f"Calcifer harness — {len(features)} features total")
    # Show phases in pipeline order
    phase_order = [
        "plan_stub", "plan_drafting", "plan_review",
        "generating", "verifying", "done", "blocked",
    ]
    for ph in phase_order:
        if ph in phase_counts:
            print(f"  {ph:14} {phase_counts[ph]}")
    print()

    # Group by phase (pipeline order), then priority, then id
    def _sort_key(f: Feature) -> tuple:
        try:
            ph_idx = phase_order.index(f.phase)
        except ValueError:
            ph_idx = 99
        return (ph_idx, PRIORITY_ORDER.get(f.priority, 99), f.id)

    sorted_feats = sorted(features, key=_sort_key)
    for f in sorted_feats:
        if f.passes:
            mark = "[x]"
        elif f.status == "in_progress":
            mark = "[~]"
        elif f.status == "blocked":
            mark = "[!]"
        else:
            mark = "[ ]"
        print(f"  {mark} [{f.priority:7}] {f.phase:14} {f.id:40} {f.title}")

    return 0


def _is_stub_feature(feature: Feature) -> bool:
    """A feature is a 'stub' if it still has the cmd_add placeholder verify.

    Stubs represent planning debt — the contract has not been filled in and
    the feature is not pickable until it is.
    """
    return any(
        cmd.strip() == PLACEHOLDER_VERIFY for cmd in feature.verification
    )


def cmd_pick(args: argparse.Namespace) -> int:
    _, features = load_features()

    # Surface in-progress features FIRST — they should be resumed before new work
    in_prog = [f for f in features if f.status == "in_progress"]
    if in_prog:
        print(f"WARNING: {len(in_prog)} feature(s) already in progress:")
        for f in in_prog:
            print(f"  {f.id} — {f.title}")
        print()
        print("Run 'harness.py resume' to continue them, or finish/block them first.")
        print()

    # Partition pending into "pickable" (real verification) and "stub" (planning debt)
    all_pending = [f for f in features if not f.passes and f.status == "pending"]
    pickable = [f for f in all_pending if not _is_stub_feature(f)]
    stubs = [f for f in all_pending if _is_stub_feature(f)]

    if stubs:
        print(f"BACKLOG NEEDS PLANNING: {len(stubs)} feature(s) still have placeholder verify:")
        for f in stubs:
            print(f"  [{f.priority:7}] {f.id} — {f.title}")
        print()
        print("These contracts are stubs. Fill in motivation/design/acceptance/verification")
        print("before any of them can be picked. Edit:")
        print("  harness/contracts/<id>.md")
        print("  harness/features.json (replace the placeholder verification array)")
        print()

    if not pickable:
        if not in_prog and not stubs:
            blocked = [f for f in features if f.status == "blocked"]
            if blocked:
                print(f"No pickable features. {len(blocked)} blocked:")
                for f in blocked:
                    print(f"  {f.id} — {f.blocked_reason or '(no reason)'}")
                return 0
            print("No pending features. All done!")
        elif stubs and not in_prog:
            print(f"No pickable features — {len(stubs)} stub(s) blocking progress.")
        return 0

    pickable.sort(key=lambda f: (PRIORITY_ORDER.get(f.priority, 99), f.id))
    top = pickable[0]

    print(f"Next feature: {top.id}")
    print(f"  Title:       {top.title}")
    print(f"  Category:    {top.category}")
    print(f"  Priority:    {top.priority}")
    print()
    print(f"  Motivation:  {top.motivation}")
    print()

    contract = contract_path(top.id)
    if contract.exists():
        print(f"  Contract:    {contract}")
        print()
        print("Read the contract, then start implementing.")
    else:
        print(f"  Contract:    MISSING ({contract})")
        print()
        print("Contract does not exist yet. Write it first:")
        print(f"  cp harness/contracts/README.md {contract}")
        print(f"  # edit {contract}")
        print(f"  git add {contract} && git commit -m 'plan: add contract for {top.id}'")

    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    data, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1

    contract = contract_path(feature.id)
    if not contract.exists():
        print(f"ERROR: contract {contract} does not exist")
        print("Write the contract before verifying.")
        return 1

    if not feature.verification:
        print(f"ERROR: feature {feature.id} has no verification commands")
        return 1

    # Review gate: contract must have been reviewed and approved
    skip_review = getattr(args, "skip_review", None)
    if skip_review:
        audit = skip_review.strip()
        if not audit:
            print("ERROR: --skip-review requires a non-empty audit reason")
            return 1
        print(f"WARNING: review gate bypassed — reason: {audit}", file=sys.stderr)
    else:
        review_ok, review_reason = _check_review_gate(feature)
        if not review_ok:
            print(f"ERROR: review gate failed: {review_reason}")
            print()
            print('(Override with --skip-review "reason" only in bootstrap/emergency cases.)')
            return 1

    # Validate + parse all commands BEFORE running any
    parsed: list[tuple[str, list[str]]] = []
    for i, cmd in enumerate(feature.verification, 1):
        argv, reason = validate_and_parse_verify_command(cmd)
        if reason is not None:
            print(f"ERROR: verification command [{i}/{len(feature.verification)}] {reason}")
            print(f"  command: {cmd}")
            print("  Allowed prefixes: grep, pytest, .venv/bin/python, python -c, python -m pytest")
            return 1
        assert argv is not None
        parsed.append((cmd, argv))

    print(f"Verifying: {feature.id}")
    print(f"  Contract: {contract}")
    print(f"  Commands: {len(feature.verification)}")
    print(f"  Timeout:  {VERIFY_TIMEOUT_S}s per command")
    print()

    failed: list[tuple[str, int]] = []
    for i, (cmd, argv) in enumerate(parsed, 1):
        print(f"  [{i}/{len(parsed)}] $ {cmd}")
        try:
            # No shell=True — argv list eliminates all shell-injection paths
            result = subprocess.run(
                argv, cwd=ROOT, capture_output=True, text=True,
                timeout=VERIFY_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            print(f"    FAIL (timeout after {VERIFY_TIMEOUT_S}s)")
            failed.append((cmd, -1))
            continue
        except FileNotFoundError as e:
            print(f"    FAIL (command not found: {e})")
            failed.append((cmd, -2))
            continue
        if result.returncode != 0:
            print(f"    FAIL (exit {result.returncode})")
            if result.stdout.strip():
                print("    stdout:")
                for line in result.stdout.strip().splitlines()[-10:]:
                    print(f"      {line}")
            if result.stderr.strip():
                print("    stderr:")
                for line in result.stderr.strip().splitlines()[-10:]:
                    print(f"      {line}")
            failed.append((cmd, result.returncode))
        else:
            print("    OK")
    print()

    if failed:
        print(f"VERIFY FAILED: {len(failed)}/{len(feature.verification)} commands failed")
        return 1

    # Cache verify success: store HEAD SHA + working tree fingerprint
    sha = git_head_sha()
    tree = working_tree_fingerprint()
    if sha:
        for f in data["features"]:
            if f["id"] == feature.id:
                f["verified_sha"] = sha
                f["verified_tree"] = tree
                break
        save_features(data)
        print(f"VERIFY PASSED: {feature.id} (cached at {sha[:10]}, tree {tree or 'unknown'})")
    else:
        print(f"VERIFY PASSED: {feature.id}")

    print()
    print(f"Next: python harness/harness.py complete {feature.id}")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    data, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1

    if feature.passes:
        print(f"Already complete: {feature.id}")
        return 0

    # Reject blocked features — they must be reset first
    if feature.status == "blocked":
        print(f"ERROR: {feature.id} is blocked: {feature.blocked_reason or '(no reason)'}")
        print(f"Resolve the block, then run: python harness/harness.py reset {feature.id}")
        return 1

    # Review gate (same as cmd_verify — must be approved before complete)
    skip_review = getattr(args, "skip_review", None)
    if skip_review:
        audit = skip_review.strip()
        if not audit:
            print("ERROR: --skip-review requires a non-empty audit reason")
            return 1
        print(f"WARNING: review gate bypassed in complete — reason: {audit}", file=sys.stderr)
    else:
        review_ok, review_reason = _check_review_gate(feature)
        if not review_ok:
            print(f"ERROR: review gate failed: {review_reason}")
            return 1

    # Verify cache check: BOTH HEAD SHA and working-tree fingerprint must match
    current_sha = git_head_sha()
    current_tree = working_tree_fingerprint()
    cache_valid = (
        feature.verified_sha
        and current_sha
        and feature.verified_sha == current_sha
        and feature.verified_tree == current_tree
    )
    if cache_valid:
        print(f"Verification cached at {current_sha[:10]} (tree {current_tree or 'unknown'}) — skipping re-run")
    else:
        if feature.verified_sha and feature.verified_sha == current_sha and feature.verified_tree != current_tree:
            print(f"Cache invalidated: working tree changed since verify (was {feature.verified_tree}, now {current_tree})")
        print(f"Running verification for {feature.id}...")
        # Propagate skip_review to the sub-verify if it was set
        verify_args = argparse.Namespace(feature_id=feature.id, skip_review=skip_review)
        verify_result = cmd_verify(verify_args)
        if verify_result != 0:
            print()
            print("Verification failed. Cannot mark as complete.")
            return 1
        # Reload data after verify (it cached sha/tree)
        data, features = load_features()
        feature = find_feature(features, args.feature_id)
        if feature is None:
            print(f"ERROR: feature {args.feature_id} vanished after verify")
            return 1

    # Progress.md touch check: must be modified since HEAD, AND only additions (append-only)
    if args.skip_progress_check:
        # Explicit bypass — require a non-empty audit string
        audit = (args.skip_progress_check or "").strip()
        if not audit:
            print("ERROR: --skip-progress-check requires a non-empty audit reason")
            print('  example: --skip-progress-check "bugfix with no behavior change"')
            return 1
        print(f"WARNING: progress.md check bypassed — reason: {audit}", file=sys.stderr)
    else:
        status_ok, reason = _progress_edits_status()
        if not status_ok:
            print()
            print(f"ERROR: {reason}")
            print('(Override with --skip-progress-check "reason" only if truly a no-change case.)')
            return 1

    # Update passes field — single write (we already reloaded after verify)
    for f in data["features"]:
        if f["id"] == feature.id:
            f["passes"] = True
            f["status"] = "done"
            break
    save_features(data)

    print()
    print(f"COMPLETE: {feature.id}")
    print()
    print("Now:")
    print(f"  git add -A && git commit -m 'feat({feature.category}): implement {feature.id}'")
    return 0


def _progress_edits_status() -> tuple[bool, str]:
    """Check that progress.md has pending edits AND they are append-only.

    Returns (True, "") if OK, (False, reason) if the check fails.
    Fails CLOSED on subprocess errors.
    """
    try:
        rel_path = str(PROGRESS_FILE.relative_to(ROOT))
    except ValueError:
        return False, "progress.md path is outside repo root"

    # Check for pending edits (tracked changes or untracked)
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", rel_path],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return False, f"could not check progress.md status ({e})"

    if not status.stdout.strip():
        return False, "harness/progress.md has no pending changes — append a session entry first"

    # Check that the diff is append-only (no removed non-whitespace lines)
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--", rel_path],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return False, f"could not diff progress.md ({e})"

    # If file was deleted or renamed, reject
    if not diff.stdout:
        # Could be brand-new untracked file — that's fine (all additions)
        return True, ""

    # Walk the unified diff. Only skip actual diff headers (not bare "---" that
    # appears as a progress.md content line / markdown horizontal rule).
    # Diff metadata forms we accept as headers:
    #   "diff --git a/... b/..."   (file header)
    #   "index <sha>..<sha> ..."   (index line)
    #   "new file mode ..."        (file mode change)
    #   "deleted file mode ..."
    #   "rename from ..."          (rename)
    #   "rename to ..."
    #   "similarity index ..."
    #   "--- a/..." or "--- /dev/null"   (old-file header)
    #   "+++ b/..." or "+++ /dev/null"   (new-file header)
    #   "@@ ..."                   (hunk header)
    in_hunk = False
    for line in diff.stdout.splitlines():
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            # Before the first hunk — everything is diff metadata
            continue
        # Inside a hunk: headers can reappear only as ' ' / '+' / '-' / '\'.
        # A bare "---" content line in progress.md will appear in the diff
        # as " ---" (context), "+---" (added), or "-\\---" ... actually
        # git emits it as "-" + the literal content. So the concrete form
        # for a removed markdown HR is "---" (the first char is '-', the
        # other two are literal content chars). This is ambiguous with
        # the "--- a/path" old-file header, BUT inside a hunk that header
        # cannot appear — old/new-file headers are pre-hunk.
        #
        # Therefore: once we're in a hunk, treat any line starting with '-'
        # (but not '--- ' with a space, just in case git emits something odd)
        # as a real removal.
        if line.startswith("-"):
            # Literal "-" followed by nothing is an empty-line removal
            # (removing a blank line). Still a removal. Reject it.
            return False, (
                "harness/progress.md was edited non-appendingly (removed or "
                "modified lines). progress.md must be append-only — old "
                "entries may not be edited or deleted."
            )

    return True, ""


# ---- Review subcommands ----

_VALID_REVIEW_STATUSES = ("approved", "changes_requested", "blocking")
_VALID_REVIEWER_KINDS = ("self", "subagent", "human", "external")

# Features allowed to bootstrap with reviewer=self (dogfood cases only)
_BOOTSTRAP_SELF_REVIEW_ALLOWED = {"harness-contract-review"}


def _reviews_dir() -> Path:
    """Directory for append-only review history logs, one JSONL per feature."""
    d = HARNESS_DIR / "reviews"
    d.mkdir(exist_ok=True)
    return d


def _append_review_history(feature_id: str, entry: dict) -> None:
    """Append a review event to the feature's per-feature JSONL log.

    This file is append-only and is NEVER cleared by cmd_reset. It becomes
    the calibration corpus for the reviewer checklist (Rec 3).
    """
    path = _reviews_dir() / f"{feature_id}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cmd_review(args: argparse.Namespace) -> int:
    """Print the review packet for a feature to stdout."""
    _, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1

    print(_render_review_packet(feature))
    return 0


def cmd_review_record(args: argparse.Namespace) -> int:
    """Record a reviewer verdict for a feature."""
    from datetime import datetime, timezone

    status = args.status
    notes = (args.notes or "").strip()
    reviewer = (args.reviewer or "").strip()

    if status not in _VALID_REVIEW_STATUSES:
        print(f"ERROR: status must be one of {_VALID_REVIEW_STATUSES}, got {status!r}")
        return 1

    if reviewer not in _VALID_REVIEWER_KINDS:
        print(f"ERROR: --reviewer must be one of {_VALID_REVIEWER_KINDS}, got {reviewer!r}")
        print()
        print("  self      = same Claude session that wrote the contract (bootstrap only)")
        print("  subagent  = separate Agent tool call with fresh context (preferred)")
        print("  human     = human reviewed the packet and typed the verdict")
        print("  external  = external review tool / CI gate")
        return 1

    data, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1

    # Reviewer-identity gate: reject 'self' for everything except the
    # bootstrap case. Self-evaluation bias is the exact failure mode
    # Article 2 identifies and the whole reason this gate exists.
    if reviewer == "self" and feature.id not in _BOOTSTRAP_SELF_REVIEW_ALLOWED:
        print(f"ERROR: reviewer='self' is only allowed for bootstrap features")
        print(f"  (currently: {sorted(_BOOTSTRAP_SELF_REVIEW_ALLOWED)})")
        print()
        print("  Self-review defeats the evaluator/generator split.")
        print("  Use --reviewer subagent and invoke the Agent tool from a fresh context.")
        return 1

    # Non-approved verdicts MUST include notes
    if status != "approved" and not notes:
        print(f"ERROR: --notes is required for status {status!r}")
        print("  Reviewers must justify changes_requested / blocking decisions.")
        return 1

    # Approved verdicts reject obviously-broken contracts
    if status == "approved":
        contract = contract_path(feature.id)
        if not contract.exists():
            print(f"ERROR: cannot approve — contract file {contract} does not exist")
            return 1
        contract_text = contract.read_text(encoding="utf-8", errors="replace")
        # Check for UNQUOTED TODO markers (backtick-quoted mentions are docs, not placeholders)
        todo_lines: list[int] = []
        for i, line in enumerate(contract_text.splitlines(), 1):
            if "TODO:" not in line:
                continue
            if "TODO:" in re.sub(r"`[^`]*`", "", line):
                todo_lines.append(i)
        if todo_lines:
            print(f"ERROR: cannot approve — contract has unquoted 'TODO:' markers on line(s): {todo_lines[:5]}")
            print("  Remove the placeholders and re-submit for review.")
            return 1

    sha = _contract_sha(feature.id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for f in data["features"]:
        if f["id"] == feature.id:
            f["review_status"] = status
            f["review_notes"] = notes
            f["reviewed_at"] = now
            f["reviewed_contract_sha"] = sha
            f["reviewer"] = reviewer
            break
    save_features(data)

    # Append-only review history log (Rec 3: calibration corpus).
    # `cmd_reset` clears the cache fields on the feature but NEVER touches
    # this history file. Over time it becomes the provenance of every review.
    _append_review_history(feature.id, {
        "ts": now,
        "status": status,
        "reviewer": reviewer,
        "contract_sha": sha,
        "notes": notes,
    })

    print(f"RECORDED: {feature.id} → {status} (reviewer={reviewer})")
    print(f"  contract sha: {sha}")
    print(f"  reviewed at:  {now}")
    if notes:
        print(f"  notes: {notes[:200]}{'...' if len(notes) > 200 else ''}")
    if status == "approved":
        print()
        print(f"Next: python harness/harness.py verify {feature.id}")
    elif status == "changes_requested":
        print()
        print("Author: edit the contract to address the notes, then re-run review.")
    else:  # blocking
        print()
        print("This feature is blocked at the plan stage. Consider:")
        print(f"  python harness/harness.py block {feature.id} --reason 'plan review rejected'")

    return 0


def cmd_review_miss(args: argparse.Namespace) -> int:
    """Record a reviewer miss — a case where approval was wrong.

    Writes to the feature's review history JSONL (append-only) as a
    'miss' entry. These entries form the calibration corpus: reading
    them surfaces what the reviewer keeps missing, so the checklist in
    harness/reviewer-checklist.md can be updated with new rules.
    """
    from datetime import datetime, timezone

    what = (args.what or "").strip()
    if not what:
        print("ERROR: --what is required — describe the missed failure mode")
        return 1

    _, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _append_review_history(feature.id, {
        "ts": now,
        "event": "review_miss",
        "what": what,
    })

    print(f"MISS RECORDED: {feature.id}")
    print(f"  at: {now}")
    print(f"  what: {what}")
    print()
    print("Next steps:")
    print("  1. Update harness/reviewer-checklist.md with a new rule that would have caught this.")
    print("  2. Commit the checklist change with a reference to this miss.")
    return 0


def _check_review_gate(feature: Feature) -> tuple[bool, str]:
    """Return (ok, reason). ok=True if review gate passes."""
    if feature.review_status != "approved":
        if not feature.review_status:
            return False, (
                f"contract has not been reviewed. Run:\n"
                f"  python harness/harness.py review {feature.id}\n"
                f"  # inspect the packet, decide, then:\n"
                f"  python harness/harness.py review-record {feature.id} --status approved --notes '...'"
            )
        return False, (
            f"review status is {feature.review_status!r}, expected 'approved'. "
            f"Re-submit after addressing reviewer notes:\n"
            f"  notes: {feature.review_notes[:300]}"
        )

    current_sha = _contract_sha(feature.id)
    if current_sha != feature.reviewed_contract_sha:
        return False, (
            f"contract has been edited since review "
            f"(reviewed sha {feature.reviewed_contract_sha}, current sha {current_sha}). "
            f"Re-review required."
        )

    return True, ""


def cmd_add(args: argparse.Namespace) -> int:
    data, features = load_features()
    if find_feature(features, args.feature_id):
        print(f"ERROR: feature {args.feature_id!r} already exists")
        return 1

    # Strict feature-id check: lowercase, digits, hyphens only
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]", args.feature_id):
        print(f"ERROR: feature id must be lowercase letters/digits/hyphens only")
        return 1

    new_feature = {
        "id": args.feature_id,
        "title": args.feature_id.replace("-", " ").title(),
        "category": "uncategorized",
        "priority": "medium",
        "description": "TODO: fill in",
        "motivation": "TODO: why are we doing this?",
        "acceptance_criteria": [
            "TODO: list concrete, verifiable assertions",
        ],
        "verification": [
            # Placeholder — edit to add import/attribute checks for new symbols
            ".venv/bin/python -c \"import calcifer; assert False, 'not implemented'\"",
            ".venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py",
        ],
        "reference": "TODO: path in claude-code-source for the reference implementation",
        "status": "pending",
        "passes": False,
        "verified_sha": "",
        "verified_tree": "",
        "blocked_reason": "",
    }
    data["features"].append(new_feature)
    save_features(data)

    # Scaffold the contract file with a minimal stub (not the full template)
    contract = contract_path(args.feature_id)
    if not contract.exists():
        stub = f"""# Feature Contract: {args.feature_id}

> NEW FEATURE — fill in every section before starting implementation.
> See `harness/contracts/README.md` for the full template and guidance.

## Motivation

TODO: one paragraph — what problem does this solve and why now?

## Claude Code Reference

TODO: concrete file paths + line numbers in
`/Users/jowang/Documents/github/claude-code-source/` that this feature maps to.
If no direct analog exists, say so explicitly.

## Scope

### 要做

- TODO

### 不做 (non-goals)

- TODO

## Design

TODO: what files change, what interfaces are added/modified, how it integrates
with existing code. No final code — just enough for a reviewer to sanity-check.

## Acceptance Criteria

- [ ] TODO: verifiable assertion 1
- [ ] TODO: verifiable assertion 2

## Verification Commands

Update `features.json` verification list to match. Prefer:
- `.venv/bin/python -c "from X import Y; assert ..."` for import/attribute checks
- `.venv/bin/python -m pytest tests/test_foo.py -q -k 'new_test_name'` for behavior
- Full mock suite at the end to catch regressions

## Rollback Plan

TODO: what to do if this turns out to be wrong scope or infeasible.
"""
        contract.write_text(stub)

    print(f"Added: {args.feature_id}")
    print(f"  features.json updated (verification has placeholder — edit it)")
    print(f"  contract scaffolded: {contract}")
    print()
    print("Next:")
    print(f"  1. Edit {contract} — fill in every section")
    print(f"  2. Edit harness/features.json — replace placeholder verification")
    print(f"  3. git add -A && git commit -m 'plan: add contract for {args.feature_id}'")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Show in-progress features and their latest progress notes."""
    _, features = load_features()
    in_progress = [f for f in features if f.status == "in_progress"]
    if not in_progress:
        print("No features are currently in progress.")
        print("Run 'pick' to start a new feature.")
        return 0

    print(f"{len(in_progress)} feature(s) in progress:")
    print()
    for f in in_progress:
        print(f"  {f.id} — {f.title}")
        print(f"    priority: {f.priority}, contract: {contract_path(f.id)}")
        if f.verified_sha:
            print(f"    verified at: {f.verified_sha[:10]}")
        print()
    print("Read the contract and the latest progress.md entries, then continue.")
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    """Mark a feature as blocked with a reason."""
    data, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1
    if feature.passes:
        print(f"ERROR: {feature.id} is already marked complete")
        return 1

    for f in data["features"]:
        if f["id"] == feature.id:
            f["status"] = "blocked"
            f["blocked_reason"] = args.reason
            break
    save_features(data)

    print(f"BLOCKED: {feature.id}")
    print(f"  reason: {args.reason}")
    print()
    print("Now:")
    print("  1. Append a session entry to harness/progress.md explaining the block")
    print(f"  2. git add -A && git commit -m 'block: {feature.id} — {args.reason[:40]}'")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Move a blocked or in_progress feature back to pending."""
    data, features = load_features()
    feature = find_feature(features, args.feature_id)
    if not feature:
        print(f"ERROR: feature {args.feature_id!r} not found")
        return 1
    if feature.passes:
        print(f"ERROR: cannot reset a completed feature")
        return 1

    for f in data["features"]:
        if f["id"] == feature.id:
            f["status"] = "pending"
            f["blocked_reason"] = ""
            f["verified_sha"] = ""
            f["verified_tree"] = ""
            f["review_status"] = ""
            f["review_notes"] = ""
            f["reviewed_at"] = ""
            f["reviewed_contract_sha"] = ""
            break
    save_features(data)

    print(f"RESET: {feature.id} → pending (verify cache + review cleared)")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Prepend a timestamped entry to progress.md (UTF-8)."""
    from datetime import date
    today = date.today().isoformat()

    # Reject multi-line / markdown-conflicting titles
    if "\n" in args.title or "\r" in args.title:
        print("ERROR: title must be a single line")
        return 1
    if args.title.strip().startswith(("#", "-", "*", "---")):
        print("ERROR: title must not start with a markdown metacharacter (#, -, *, ---)")
        return 1

    if not PROGRESS_FILE.exists():
        print(f"ERROR: {PROGRESS_FILE} does not exist")
        return 1

    existing = PROGRESS_FILE.read_text(encoding="utf-8")
    # Find the first "## " heading and insert before it
    lines = existing.splitlines(keepends=True)
    insert_at = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break

    entry_lines = [
        f"## {today} — {args.title}\n",
        "\n",
        f"{args.body}\n" if args.body else "",
        "\n",
        "---\n",
        "\n",
    ]

    if insert_at is None:
        new_content = existing.rstrip() + "\n\n" + "".join(entry_lines)
    else:
        new_content = "".join(lines[:insert_at]) + "".join(entry_lines) + "".join(lines[insert_at:])

    PROGRESS_FILE.write_text(new_content, encoding="utf-8")
    print(f"Logged to {PROGRESS_FILE}: {today} — {args.title}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness.py", description="Calcifer harness CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show feature backlog summary")
    sub.add_parser("pick", help="Pick the highest-priority pending feature")
    sub.add_parser("resume", help="Show in-progress features")

    p_verify = sub.add_parser("verify", help="Run verification for a feature")
    p_verify.add_argument("feature_id")
    p_verify.add_argument(
        "--skip-review",
        default=None,
        metavar="REASON",
        help=(
            "Bypass the contract-review gate. REQUIRES a non-empty audit "
            "reason string. Use only in bootstrap or emergency cases. "
            "The reason is printed to stderr."
        ),
    )

    p_complete = sub.add_parser("complete", help="Mark a feature as passing")
    p_complete.add_argument("feature_id")
    p_complete.add_argument(
        "--skip-progress-check",
        default=None,
        metavar="REASON",
        help=(
            "Allow complete without a progress.md edit. REQUIRES a non-empty "
            "audit reason string. The reason is printed to stderr for logging. "
            "Example: --skip-progress-check 'pure whitespace refactor'"
        ),
    )
    p_complete.add_argument(
        "--skip-review",
        default=None,
        metavar="REASON",
        help=(
            "Bypass the contract-review gate. REQUIRES a non-empty audit "
            "reason string. Also propagates to the internal verify call. "
            "Use only in bootstrap or emergency cases."
        ),
    )

    p_add = sub.add_parser("add", help="Add a new feature to the backlog")
    p_add.add_argument("feature_id")

    p_block = sub.add_parser("block", help="Mark a feature as blocked")
    p_block.add_argument("feature_id")
    p_block.add_argument("--reason", required=True, help="Why is this feature blocked?")

    p_reset = sub.add_parser("reset", help="Reset a blocked/in_progress feature to pending")
    p_reset.add_argument("feature_id")

    p_log = sub.add_parser("log", help="Prepend a dated entry to progress.md")
    p_log.add_argument("title", help="Entry heading (one line)")
    p_log.add_argument("--body", default="", help="Entry body (optional)")

    p_review = sub.add_parser(
        "review",
        help="Print the review packet for a feature's contract",
    )
    p_review.add_argument("feature_id")

    p_review_record = sub.add_parser(
        "review-record",
        help="Record a reviewer verdict for a feature",
    )
    p_review_record.add_argument("feature_id")
    p_review_record.add_argument(
        "--status",
        required=True,
        choices=_VALID_REVIEW_STATUSES,
        help="Review verdict",
    )
    p_review_record.add_argument(
        "--reviewer",
        required=True,
        choices=_VALID_REVIEWER_KINDS,
        help=(
            "Who reviewed this contract. 'self' is rejected for non-bootstrap "
            "features — the whole point of the gate is independence."
        ),
    )
    p_review_record.add_argument(
        "--notes",
        default="",
        help="Reviewer feedback (required for non-approved verdicts)",
    )

    p_review_miss = sub.add_parser(
        "review-miss",
        help="Record a case where the reviewer approved something that turned out wrong",
    )
    p_review_miss.add_argument("feature_id")
    p_review_miss.add_argument(
        "--what",
        required=True,
        help="Describe the failure mode the reviewer missed",
    )

    args = parser.parse_args()

    handlers = {
        "status": cmd_status,
        "pick": cmd_pick,
        "resume": cmd_resume,
        "verify": cmd_verify,
        "complete": cmd_complete,
        "add": cmd_add,
        "block": cmd_block,
        "reset": cmd_reset,
        "log": cmd_log,
        "review": cmd_review,
        "review-record": cmd_review_record,
        "review-miss": cmd_review_miss,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
