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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HARNESS_DIR = Path(__file__).parent
ROOT = HARNESS_DIR.parent
FEATURES_FILE = HARNESS_DIR / "features.json"
CONTRACTS_DIR = HARNESS_DIR / "contracts"
PROGRESS_FILE = HARNESS_DIR / "progress.md"

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


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
    with FEATURES_FILE.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


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
    # Find highest-priority pending feature
    pending = [f for f in features if not f.passes and f.status == "pending"]
    if not pending:
        # Check if anything is in_progress
        in_prog = [f for f in features if f.status == "in_progress"]
        if in_prog:
            print(f"No pending features. {len(in_prog)} in progress:")
            for f in in_prog:
                print(f"  {f.id} — {f.title}")
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
    _, features = load_features()
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

    print(f"Verifying: {feature.id}")
    print(f"  Contract: {contract}")
    print(f"  Commands: {len(feature.verification)}")
    print()

    failed: list[tuple[str, int]] = []
    for i, cmd in enumerate(feature.verification, 1):
        print(f"  [{i}/{len(feature.verification)}] $ {cmd}")
        result = subprocess.run(
            cmd, shell=True, cwd=ROOT, capture_output=True, text=True,
        )
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

    # Re-run verification as a safety gate
    print(f"Running verification for {feature.id} before marking complete...")
    verify_result = cmd_verify(argparse.Namespace(feature_id=feature.id))
    if verify_result != 0:
        print()
        print("Verification failed. Cannot mark as complete.")
        return 1

    # Update passes field in place
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
    print("  1. Append a session entry to harness/progress.md")
    print(f"  2. git add -A && git commit -m 'feat({feature.category}): implement {feature.id}'")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    data, features = load_features()
    if find_feature(features, args.feature_id):
        print(f"ERROR: feature {args.feature_id!r} already exists")
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
            "pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py",
        ],
        "reference": "TODO: path in claude-code-source for the reference implementation",
        "status": "pending",
        "passes": False,
    }
    data["features"].append(new_feature)
    save_features(data)

    # Scaffold the contract file
    contract = contract_path(args.feature_id)
    if not contract.exists():
        template = (CONTRACTS_DIR / "README.md").read_text()
        contract.write_text(template.replace("<feature-id>", args.feature_id))

    print(f"Added: {args.feature_id}")
    print(f"  features.json updated")
    print(f"  contract scaffolded: {contract}")
    print()
    print("Next: edit both to fill in the details, then commit.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness.py", description="Calcifer harness CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show feature backlog summary")
    sub.add_parser("pick", help="Pick the highest-priority pending feature")

    p_verify = sub.add_parser("verify", help="Run verification for a feature")
    p_verify.add_argument("feature_id")

    p_complete = sub.add_parser("complete", help="Mark a feature as passing")
    p_complete.add_argument("feature_id")

    p_add = sub.add_parser("add", help="Add a new feature to the backlog")
    p_add.add_argument("feature_id")

    args = parser.parse_args()

    handlers = {
        "status": cmd_status,
        "pick": cmd_pick,
        "verify": cmd_verify,
        "complete": cmd_complete,
        "add": cmd_add,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
