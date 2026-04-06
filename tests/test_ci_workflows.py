"""Tests for the GitHub Actions workflows under .github/workflows/.

These tests parse the workflow YAML and assert structural invariants
the project relies on: the Python+OS test matrix, OIDC publish config,
and the README CI badge. They run in CI itself, so PyYAML is added to
the workflow install line in ci.yml — without it CI would ImportError
on its own self-test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_YML = ROOT / ".github" / "workflows" / "publish.yml"


def test_ci_workflow_exists_and_parses():
    """ci.yml exists, parses, and the test job matrix covers the
    supported Python versions and both Linux + macOS."""
    assert CI_YML.exists(), f"ci.yml missing at {CI_YML}"

    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    matrix = data["jobs"]["test"]["strategy"]["matrix"]

    expected_pys = {"3.11", "3.12", "3.13"}
    assert expected_pys.issubset(set(matrix["python-version"])), (
        f"ci.yml matrix python-version missing entries: "
        f"{expected_pys - set(matrix['python-version'])}"
    )

    expected_os = {"ubuntu-latest", "macos-latest"}
    assert expected_os.issubset(set(matrix["os"])), (
        f"ci.yml matrix os missing entries: "
        f"{expected_os - set(matrix['os'])}"
    )


def test_publish_workflow_exists_and_uses_oidc():
    """publish.yml exists, parses, triggers on v* tags, has OIDC
    permissions, references the canonical PyPA action, and contains
    no `password:` field (which would mean an API token is in use)."""
    assert PUBLISH_YML.exists(), f"publish.yml missing at {PUBLISH_YML}"

    raw = PUBLISH_YML.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    # PyYAML parses YAML's `on:` key as Python True (bool). Look up
    # by either the string or the bool to be robust to either.
    on_section = data.get("on") or data.get(True)
    assert on_section is not None, "publish.yml missing `on:` trigger"
    tags = on_section["push"]["tags"]
    assert "v*" in tags, f"publish.yml must trigger on tag v*, got tags={tags}"

    job = data["jobs"]["build-and-publish"]
    assert job["permissions"]["id-token"] == "write", (
        "publish.yml job must have permissions.id-token: write for OIDC"
    )

    assert "pypa/gh-action-pypi-publish" in raw, (
        "publish.yml must reference pypa/gh-action-pypi-publish"
    )
    assert "password:" not in raw, (
        "publish.yml must not contain a `password:` field — OIDC "
        "trusted publishing requires no API token"
    )


def test_readme_has_ci_badge():
    """README.md must show the CI badge so visitors see build state."""
    readme = ROOT / "README.md"
    assert readme.exists(), f"README.md missing at {readme}"
    text = readme.read_text(encoding="utf-8")
    assert "actions/workflows/ci.yml/badge.svg" in text, (
        "README.md missing CI badge linking to ci.yml/badge.svg"
    )
