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
    verified_sha: str = ""
    blocked_reason: str = ""

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
            blocked_reason=d.get("blocked_reason", ""),
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
            "blocked_reason": self.blocked_reason,
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


def validate_verify_command(cmd: str) -> str | None:
    """Check if a verify command matches the allow-list.

    Returns None if OK, or a string explaining why it's rejected.

    shlex.split tokenizes the command respecting quotes, so a semicolon
    inside a quoted Python string (`python -c "a; b"`) becomes part of
    the token, not its own token. We reject only tokens that are
    unquoted shell metacharacters.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError as e:
        return f"rejected: unparseable ({e})"

    if not tokens:
        return "rejected: empty command"

    # Reject unquoted shell metacharacters (would appear as their own token)
    FORBIDDEN_TOKENS = {";", "&&", "||", "|", ">", "<", ">>", "<<", "&"}
    for tok in tokens:
        if tok in FORBIDDEN_TOKENS:
            return f"rejected: unquoted shell metacharacter {tok!r}"
        # Reject command substitution
        if "`" in tok or "$(" in tok:
            return f"rejected: command substitution in {tok!r}"

    for prefix in VERIFY_ALLOWLIST:
        if len(tokens) >= len(prefix) and tokens[: len(prefix)] == prefix:
            return None

    return f"rejected: command prefix not in allow-list (got {tokens[:2]!r})"


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

    done = sum(1 for f in features if f.passes)
    in_progress = sum(1 for f in features if f.status == "in_progress")
    blocked = sum(1 for f in features if f.status == "blocked")
    pending = sum(1 for f in features if not f.passes and f.status == "pending")

    print(f"Calcifer harness — {len(features)} features total")
    print(f"  done:        {done}")
    print(f"  in_progress: {in_progress}")
    print(f"  blocked:     {blocked}")
    print(f"  pending:     {pending}")
    print()

    # Group by status
    sorted_feats = sorted(
        features,
        key=lambda f: (
            0 if f.passes else (1 if f.status == "in_progress" else (2 if f.status == "blocked" else 3)),
            PRIORITY_ORDER.get(f.priority, 99),
            f.id,
        ),
    )
    for f in sorted_feats:
        if f.passes:
            mark = "[x]"
        elif f.status == "in_progress":
            mark = "[~]"
        elif f.status == "blocked":
            mark = "[!]"
        else:
            mark = "[ ]"
        print(f"  {mark} [{f.priority:7}] {f.id:40} {f.title}")

    return 0


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

    # Find highest-priority pending feature
    pending = [f for f in features if not f.passes and f.status == "pending"]
    if not pending:
        if not in_prog:
            blocked = [f for f in features if f.status == "blocked"]
            if blocked:
                print(f"No pending features. {len(blocked)} blocked:")
                for f in blocked:
                    print(f"  {f.id} — {f.blocked_reason or '(no reason)'}")
                return 0
            print("No pending features. All done!")
        return 0

    pending.sort(key=lambda f: (PRIORITY_ORDER.get(f.priority, 99), f.id))
    top = pending[0]

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

    # Validate all commands against the allow-list BEFORE running any
    for cmd in feature.verification:
        reason = validate_verify_command(cmd)
        if reason is not None:
            print(f"ERROR: verification command {reason}")
            print(f"  command: {cmd}")
            print("  Allowed prefixes: grep, pytest, .venv/bin/python, python -c, python -m pytest")
            return 1

    print(f"Verifying: {feature.id}")
    print(f"  Contract: {contract}")
    print(f"  Commands: {len(feature.verification)}")
    print(f"  Timeout:  {VERIFY_TIMEOUT_S}s per command")
    print()

    failed: list[tuple[str, int]] = []
    for i, cmd in enumerate(feature.verification, 1):
        print(f"  [{i}/{len(feature.verification)}] $ {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=ROOT, capture_output=True, text=True,
                timeout=VERIFY_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            print(f"    FAIL (timeout after {VERIFY_TIMEOUT_S}s)")
            failed.append((cmd, -1))
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

    # Cache verify success: store current HEAD SHA against the feature
    sha = git_head_sha()
    if sha:
        for f in data["features"]:
            if f["id"] == feature.id:
                f["verified_sha"] = sha
                break
        save_features(data)
        print(f"VERIFY PASSED: {feature.id} (cached at {sha[:10]})")
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

    # Verify cache check: if verified_sha matches current HEAD, skip re-run
    current_sha = git_head_sha()
    if feature.verified_sha and current_sha and feature.verified_sha == current_sha:
        print(f"Verification already cached at {current_sha[:10]} — skipping re-run")
    else:
        # Re-run verification
        print(f"Running verification for {feature.id}...")
        verify_result = cmd_verify(argparse.Namespace(feature_id=feature.id))
        if verify_result != 0:
            print()
            print("Verification failed. Cannot mark as complete.")
            return 1
        # Reload data after verify (which may have cached the SHA)
        data, features = load_features()
        feature = find_feature(features, args.feature_id)

    # Progress.md touch check: must be modified since HEAD or as uncommitted change
    if not args.skip_progress_check:
        progress_changed = _progress_has_pending_edits()
        if not progress_changed:
            print()
            print("ERROR: harness/progress.md has no pending changes.")
            print("Append a session entry describing what was done, then re-run complete.")
            print("(Override with --skip-progress-check if truly a no-change case.)")
            return 1

    # Update passes field in place (re-read file to avoid race)
    data, _ = load_features()
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


def _progress_has_pending_edits() -> bool:
    """Return True if progress.md has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(PROGRESS_FILE.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return True  # Can't check → don't block


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
            break
    save_features(data)

    print(f"RESET: {feature.id} → pending")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Prepend a timestamped entry to progress.md."""
    from datetime import date
    today = date.today().isoformat()

    if not PROGRESS_FILE.exists():
        print(f"ERROR: {PROGRESS_FILE} does not exist")
        return 1

    existing = PROGRESS_FILE.read_text()
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
        # No existing entries — append at end
        new_content = existing.rstrip() + "\n\n" + "".join(entry_lines)
    else:
        new_content = "".join(lines[:insert_at]) + "".join(entry_lines) + "".join(lines[insert_at:])

    PROGRESS_FILE.write_text(new_content)
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

    p_complete = sub.add_parser("complete", help="Mark a feature as passing")
    p_complete.add_argument("feature_id")
    p_complete.add_argument(
        "--skip-progress-check",
        action="store_true",
        help="Allow complete without a progress.md edit (use sparingly)",
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
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
