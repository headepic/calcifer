"""Tests for harness contract review mechanism.

Covers the review/review-record subcommands, the verify/complete review
gate, and the --skip-review escape hatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the harness module by path since it lives outside the calcifer package
_HARNESS_DIR = Path(__file__).parent.parent / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import harness as h  # type: ignore[import-not-found]


@pytest.fixture
def tmp_harness(tmp_path, monkeypatch):
    """Isolated harness instance with its own features.json + contracts dir."""
    hdir = tmp_path / "harness"
    hdir.mkdir()
    (hdir / "contracts").mkdir()
    features_file = hdir / "features.json"
    progress_file = hdir / "progress.md"
    progress_file.write_text("# Progress\n", encoding="utf-8")

    # Patch module-level paths to point at the tmp harness
    monkeypatch.setattr(h, "HARNESS_DIR", hdir)
    monkeypatch.setattr(h, "ROOT", tmp_path)
    monkeypatch.setattr(h, "FEATURES_FILE", features_file)
    monkeypatch.setattr(h, "CONTRACTS_DIR", hdir / "contracts")
    monkeypatch.setattr(h, "PROGRESS_FILE", progress_file)

    # Seed features.json with version + empty features list
    features_file.write_text(
        json.dumps({"version": 1, "features": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "root": tmp_path,
        "hdir": hdir,
        "features": features_file,
        "contracts": hdir / "contracts",
    }


def _add_feature(paths, feature_id="test-feature", with_contract=True):
    """Helper: add a feature with a sensible default contract."""
    data = json.loads(paths["features"].read_text(encoding="utf-8"))
    data["features"].append({
        "id": feature_id,
        "title": feature_id.replace("-", " ").title(),
        "category": "test",
        "priority": "medium",
        "description": "A test feature",
        "motivation": "For testing the review mechanism",
        "acceptance_criteria": [
            "Criterion 1",
            "Criterion 2",
            "Criterion 3",
        ],
        # Use grep against the contract file itself — works from any cwd that
        # has harness/contracts/<id>.md. The contract is created below.
        "verification": [
            f"grep -q 'Feature Contract' harness/contracts/{feature_id}.md",
        ],
        "reference": "No direct analog.",
        "status": "pending",
        "passes": False,
        "verified_sha": "",
        "verified_tree": "",
        "blocked_reason": "",
        "review_status": "",
        "review_notes": "",
        "reviewed_at": "",
        "reviewed_contract_sha": "",
    })
    paths["features"].write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if with_contract:
        contract = paths["contracts"] / f"{feature_id}.md"
        contract.write_text(
            f"# Feature Contract: {feature_id}\n\n"
            "## Motivation\n\nReal motivation text that is long enough to pass the sanity check.\n\n"
            "## Claude Code Reference\n\nNo direct analog — this is a test feature.\n\n"
            "## Scope\n\n### 要做\n\n- Do the thing\n\n### 不做\n\n- Anything else\n\n"
            "## Design\n\nSimple design with enough text to satisfy the section length check.\n\n"
            "## Acceptance Criteria\n\n- [ ] Criterion 1\n- [ ] Criterion 2\n- [ ] Criterion 3\n\n"
            "## Verification Commands\n\n```\n.venv/bin/python -c \"assert True\"\n```\n\n"
            "## Rollback Plan\n\nRevert the commit.\n",
            encoding="utf-8",
        )


# -------- review-record behavior --------


def test_review_record_approves_and_gates(tmp_harness):
    """After review-record approved, the review gate passes."""
    _add_feature(tmp_harness, "feat-a")

    args = argparse.Namespace(feature_id="feat-a", status="approved", reviewer="subagent", notes="LGTM")
    rc = h.cmd_review_record(args)
    assert rc == 0

    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    feat_dict = data["features"][0]
    assert feat_dict["review_status"] == "approved"
    assert feat_dict["reviewed_contract_sha"]  # non-empty
    assert feat_dict["reviewed_at"]  # non-empty ISO timestamp

    # Check that _check_review_gate now passes
    feature = h.Feature.from_dict(feat_dict)
    ok, reason = h._check_review_gate(feature)
    assert ok is True, f"gate should pass, got reason: {reason}"


def test_review_record_rejects_without_notes(tmp_harness):
    """changes_requested and blocking require non-empty notes."""
    _add_feature(tmp_harness, "feat-b")

    # Empty notes → rejected
    args = argparse.Namespace(feature_id="feat-b", status="changes_requested", reviewer="subagent", notes="")
    rc = h.cmd_review_record(args)
    assert rc == 1

    # Whitespace-only notes → rejected
    args = argparse.Namespace(feature_id="feat-b", status="blocking", reviewer="subagent", notes="   ")
    rc = h.cmd_review_record(args)
    assert rc == 1

    # With notes → accepted
    args = argparse.Namespace(feature_id="feat-b", status="changes_requested", reviewer="subagent", notes="scope too broad, split into two features")
    rc = h.cmd_review_record(args)
    assert rc == 0


def test_review_record_detects_contract_edit(tmp_harness):
    """Editing the contract after approval invalidates the gate via SHA mismatch."""
    _add_feature(tmp_harness, "feat-c")

    # Approve
    rc = h.cmd_review_record(
        argparse.Namespace(feature_id="feat-c", status="approved", reviewer="subagent", notes="ok")
    )
    assert rc == 0

    # Edit the contract
    contract = tmp_harness["contracts"] / "feat-c.md"
    contract.write_text(
        contract.read_text(encoding="utf-8") + "\n\n## Extra section\n\nEdited content.\n",
        encoding="utf-8",
    )

    # Gate should now reject: SHA mismatch
    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    feature = h.Feature.from_dict(data["features"][0])
    ok, reason = h._check_review_gate(feature)
    assert ok is False
    assert "edited since review" in reason.lower() or "sha" in reason.lower()


def test_review_record_rejects_approved_with_todo(tmp_harness):
    """An unquoted TODO: in the contract blocks approval."""
    _add_feature(tmp_harness, "feat-d")

    # Inject a real TODO placeholder into the contract (NOT backtick-quoted)
    contract = tmp_harness["contracts"] / "feat-d.md"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            "Real motivation text",
            "TODO: fill in the real motivation",
        ),
        encoding="utf-8",
    )

    rc = h.cmd_review_record(
        argparse.Namespace(feature_id="feat-d", status="approved", reviewer="subagent", notes="")
    )
    assert rc == 1, "approval should be rejected due to TODO: marker"

    # Verify the feature was NOT approved
    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    assert data["features"][0]["review_status"] == ""


def test_review_record_allows_backtick_quoted_todo(tmp_harness):
    """TODO: inside backticks is documentation, not a placeholder — allowed."""
    _add_feature(tmp_harness, "feat-e")

    contract = tmp_harness["contracts"] / "feat-e.md"
    contract.write_text(
        contract.read_text(encoding="utf-8") +
        "\nNote: do not leave literal `TODO:` markers in the contract.\n",
        encoding="utf-8",
    )

    rc = h.cmd_review_record(
        argparse.Namespace(feature_id="feat-e", status="approved", reviewer="subagent", notes="ok")
    )
    assert rc == 0, "approval should succeed when TODO: is backtick-quoted"


# -------- verify gate behavior --------


def test_verify_refuses_without_approved_review(tmp_harness):
    """cmd_verify fails with a clear error when review_status is empty."""
    _add_feature(tmp_harness, "feat-f")

    args = argparse.Namespace(feature_id="feat-f", skip_review=None)
    rc = h.cmd_verify(args)
    assert rc == 1  # gate should refuse


def test_verify_passes_after_approved_review(tmp_harness, capsys):
    """After approval, the review gate no longer blocks verify."""
    _add_feature(tmp_harness, "feat-g")

    # Approve
    h.cmd_review_record(
        argparse.Namespace(feature_id="feat-g", status="approved", reviewer="subagent", notes="ok")
    )

    # Verify should now PASS the review gate.
    # Verification command runs (it's `python -c "assert True"` which succeeds).
    args = argparse.Namespace(feature_id="feat-g", skip_review=None)
    rc = h.cmd_verify(args)
    # The verify result also depends on git_head_sha cache writing; we care
    # about the gate, not the cache. rc 0 means gate + command both OK.
    assert rc == 0, f"verify should succeed, rc={rc}"


def test_skip_review_with_reason(tmp_harness):
    """--skip-review REASON bypasses the gate."""
    _add_feature(tmp_harness, "feat-h")

    # Without skip: gate rejects
    rc = h.cmd_verify(argparse.Namespace(feature_id="feat-h", skip_review=None))
    assert rc == 1

    # With skip + reason: gate passes, verification runs
    rc = h.cmd_verify(argparse.Namespace(feature_id="feat-h", skip_review="bootstrap"))
    assert rc == 0

    # With skip + empty reason: rejected
    rc = h.cmd_verify(argparse.Namespace(feature_id="feat-h", skip_review="   "))
    assert rc == 1


def test_reset_clears_review_fields(tmp_harness):
    """cmd_reset clears review_status, review_notes, reviewed_at, reviewed_contract_sha."""
    _add_feature(tmp_harness, "feat-i")

    # Approve, then block, then reset
    h.cmd_review_record(
        argparse.Namespace(feature_id="feat-i", status="approved", reviewer="subagent", notes="ok")
    )

    # Confirm fields are populated
    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    f0 = data["features"][0]
    assert f0["review_status"] == "approved"
    assert f0["reviewed_contract_sha"]
    assert f0["reviewed_at"]

    # Block it (to move out of pending, so reset has something to do)
    h.cmd_block(argparse.Namespace(feature_id="feat-i", reason="testing reset"))

    # Reset
    rc = h.cmd_reset(argparse.Namespace(feature_id="feat-i"))
    assert rc == 0

    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    f0 = data["features"][0]
    assert f0["status"] == "pending"
    assert f0["review_status"] == ""
    assert f0["review_notes"] == ""
    assert f0["reviewed_at"] == ""
    assert f0["reviewed_contract_sha"] == ""
    assert f0["verified_sha"] == ""
    assert f0["verified_tree"] == ""


# -------- Feature dataclass fields --------


def test_feature_dataclass_has_review_fields():
    """All five review-related fields exist on the Feature dataclass."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(h.Feature)}
    assert "review_status" in field_names
    assert "review_notes" in field_names
    assert "reviewed_at" in field_names
    assert "reviewed_contract_sha" in field_names
    assert "reviewer" in field_names


# -------- Reviewer identity gate (Rec 1) --------


def test_review_record_rejects_reviewer_self_for_non_bootstrap(tmp_harness):
    """reviewer='self' is rejected except for the bootstrap allowlist."""
    _add_feature(tmp_harness, "feat-j")

    rc = h.cmd_review_record(argparse.Namespace(
        feature_id="feat-j", status="approved",
        reviewer="self", notes="self-reviewing",
    ))
    assert rc == 1, "self-review should be rejected for non-bootstrap features"


def test_review_record_allows_reviewer_self_for_bootstrap(tmp_harness):
    """Bootstrap features in _BOOTSTRAP_SELF_REVIEW_ALLOWED may self-review."""
    _add_feature(tmp_harness, "harness-contract-review")
    rc = h.cmd_review_record(argparse.Namespace(
        feature_id="harness-contract-review", status="approved",
        reviewer="self", notes="bootstrap",
    ))
    assert rc == 0


# -------- Phase state machine (Rec 5) --------


def test_feature_phase_plan_stub(tmp_harness):
    """A feature with the placeholder verify is in plan_stub phase."""
    data = json.loads(tmp_harness["features"].read_text(encoding="utf-8"))
    data["features"].append({
        "id": "stub",
        "title": "x", "category": "c", "priority": "low",
        "description": "", "motivation": "", "acceptance_criteria": [],
        "verification": [h.PLACEHOLDER_VERIFY],
        "reference": "", "status": "pending", "passes": False,
    })
    tmp_harness["features"].write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )
    _, features = h.load_features()
    assert features[0].phase == "plan_stub"


def test_feature_phase_progression():
    """Phase derives correctly from review_status and verify cache fields."""
    f = h.Feature(
        id="x", title="", category="", priority="medium",
        description="", motivation="", acceptance_criteria=[],
        verification=[".venv/bin/python -c 'pass'"],
        reference="", status="pending", passes=False,
    )
    assert f.phase == "plan_drafting"

    f.review_status = "changes_requested"
    assert f.phase == "plan_review"

    f.review_status = "approved"
    assert f.phase == "generating"

    f.verified_sha = "abc123"
    f.verified_tree = "def456"
    assert f.phase == "verifying"

    f.passes = True
    assert f.phase == "done"


# -------- Placeholder verify rejection (Rec 7) --------


def test_placeholder_verify_command_rejected():
    """validate_and_parse_verify_command rejects the cmd_add placeholder."""
    argv, err = h.validate_and_parse_verify_command(h.PLACEHOLDER_VERIFY)
    assert argv is None
    assert err is not None
    assert "placeholder" in err.lower()


# -------- Append-only review history + review-miss (Rec 3) --------


def test_review_history_append_only(tmp_harness):
    """review-record appends to review history JSONL; reset does not wipe it."""
    _add_feature(tmp_harness, "feat-k")

    h.cmd_review_record(argparse.Namespace(
        feature_id="feat-k", status="changes_requested",
        reviewer="subagent", notes="criterion 3 is vague",
    ))
    h.cmd_review_record(argparse.Namespace(
        feature_id="feat-k", status="approved",
        reviewer="subagent", notes="looks good now",
    ))

    history_file = tmp_harness["hdir"] / "reviews" / "feat-k.jsonl"
    assert history_file.exists()
    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["status"] == "changes_requested"
    assert second["status"] == "approved"
    assert first["reviewer"] == "subagent"

    # Reset must NOT wipe the history
    h.cmd_block(argparse.Namespace(feature_id="feat-k", reason="testing"))
    h.cmd_reset(argparse.Namespace(feature_id="feat-k"))

    assert history_file.exists()
    lines_after = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines_after) == 2, "reset must not touch review history"


def test_review_miss_command(tmp_harness):
    """review-miss records a calibration entry in the history file."""
    _add_feature(tmp_harness, "feat-l")

    rc = h.cmd_review_miss(argparse.Namespace(
        feature_id="feat-l",
        what="approved a contract that added an already-existing symbol",
    ))
    assert rc == 0

    history_file = tmp_harness["hdir"] / "reviews" / "feat-l.jsonl"
    assert history_file.exists()
    entry = json.loads(history_file.read_text(encoding="utf-8").strip())
    assert entry["event"] == "review_miss"
    assert "already-existing" in entry["what"]
