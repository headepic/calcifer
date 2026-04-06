"""Tests for Calcifer's packaging metadata.

These tests guarantee the package ships the artifacts an SDK consumer
expects: PEP 561 type marker, proper hatch wheel config, etc.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root (resolve via tests dir)
ROOT = Path(__file__).parent.parent

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover — fallback for older Python
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


def test_py_typed_marker_present():
    """PEP 561: calcifer must ship a py.typed marker file inside the package.

    Without this, `mypy` and `pyright` treat `from calcifer import ...` as
    untyped (every symbol becomes Any). The marker is an empty file whose
    mere presence is the signal.
    """
    import calcifer
    package_dir = Path(calcifer.__file__).parent
    marker = package_dir / "py.typed"
    assert marker.exists(), (
        f"py.typed marker missing at {marker}. "
        f"Without it, downstream mypy/pyright cannot use calcifer's type hints."
    )


def test_pyproject_declares_py_typed_in_wheel():
    """The hatch wheel build config must include calcifer (so py.typed ships).

    A lazy implementation could create the py.typed file on disk without
    updating pyproject.toml — the runtime test would pass but the BUILT
    wheel would not contain the marker. This test parses pyproject.toml
    and asserts the hatch build config explicitly lists the calcifer
    package, which causes hatchling to walk the directory tree and
    include py.typed automatically.
    """
    pyproject_path = ROOT / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml missing at {pyproject_path}"

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    # Drill down to [tool.hatch.build.targets.wheel]
    wheel_cfg = (
        data.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("wheel", {})
    )
    assert wheel_cfg, (
        "pyproject.toml has no [tool.hatch.build.targets.wheel] section. "
        "Without it, hatchling does not know which package to include in the "
        "wheel and the py.typed marker may be omitted."
    )

    packages = wheel_cfg.get("packages", [])
    assert "calcifer" in packages, (
        f"[tool.hatch.build.targets.wheel] packages={packages} does not "
        f"include 'calcifer'. The py.typed marker will not ship."
    )


# ────────────────────────────────────────────────────────────────────
# pyproject.toml metadata for PyPI publishing (sdk-pyproject-metadata)
# ────────────────────────────────────────────────────────────────────


def _load_pyproject() -> dict:
    """Helper: parse pyproject.toml from repo root."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_has_required_metadata():
    """[project] table must have all PyPI-required fields populated."""
    data = _load_pyproject()
    project = data.get("project", {})

    required_fields = {
        "name", "version", "description", "readme", "license",
        "authors", "keywords", "classifiers", "requires-python",
    }
    missing = [f for f in required_fields if not project.get(f)]
    assert not missing, f"pyproject.toml [project] missing fields: {missing}"

    # Version is the dev marker (PEP 440)
    assert project["version"] == "0.3.0.dev0", (
        f"version should be '0.3.0.dev0', got {project['version']!r}"
    )

    # [project.urls] section with the four canonical URLs
    urls = project.get("urls") or {}
    required_urls = {"Homepage", "Repository", "Issues", "Changelog"}
    missing_urls = required_urls - set(urls.keys())
    assert not missing_urls, (
        f"[project.urls] missing keys: {missing_urls}. Got: {sorted(urls.keys())}"
    )


def test_pyproject_classifiers_cover_python_versions():
    """Classifiers must enumerate Python 3.11, 3.12, and 3.13."""
    data = _load_pyproject()
    classifiers = data["project"]["classifiers"]

    expected = {
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    }
    missing = expected - set(classifiers)
    assert not missing, f"classifiers missing Python version entries: {missing}"

    # MIT license classifier
    assert "License :: OSI Approved :: MIT License" in classifiers, (
        "MIT license classifier missing"
    )

    # Typing :: Typed pairs with the py.typed marker
    assert "Typing :: Typed" in classifiers, (
        "'Typing :: Typed' classifier missing — should pair with py.typed marker"
    )


def test_license_file_exists():
    """A LICENSE file must exist at the repo root with real MIT text."""
    license_path = ROOT / "LICENSE"
    assert license_path.exists(), f"LICENSE file missing at {license_path}"

    text = license_path.read_text(encoding="utf-8")
    assert len(text) >= 500, f"LICENSE is suspiciously small: {len(text)} bytes"
    # Sanity check it actually looks like an MIT license
    assert "MIT License" in text, "LICENSE doesn't contain 'MIT License' header"
    assert "Permission is hereby granted" in text, "LICENSE missing MIT permission clause"


# ────────────────────────────────────────────────────────────────────
# Public API surface lockdown (sdk-public-api-audit)
# ────────────────────────────────────────────────────────────────────

# THE canonical list of names calcifer commits to keeping stable.
# Editing this set is a SEMVER EVENT — see docs/public-api.md.
# This snapshot test fails if `calcifer.__all__` drifts; the
# contributor must update __all__, this set, AND docs/public-api.md
# in the same commit.
_EXPECTED_PUBLIC_API: frozenset[str] = frozenset({
    # Core (4)
    "Agent", "AgentResult", "CalciferConfig", "MCPServerConfig",
    # Tool API (7)
    "Tool", "FunctionTool", "ToolContext", "ToolResult",
    "ValidationResult", "tool", "find_tool_by_name",
    # Tool registry (3)
    "get_all_builtin_tools", "get_tools", "assemble_tool_pool",
    # Messages (5)
    "Message", "ToolCall", "Usage", "StreamEvent", "APIErrorType",
    # Errors (1)
    "LLMProviderError",
    # Settings (1)
    "load_settings",
    # Provisional: Multi-agent (2)
    "Coordinator", "CoordinatorConfig",
    # Provisional: Context (1)
    "ContextManager",
    # Provisional: Hooks (3)
    "HookManager", "HookConfig", "HookEvent",
    # Provisional: Transport (1)
    "LLMProvider",
    # Lower priority (3)
    "CostTracker", "MetricsManager", "run_tools",
})


def test_public_api_surface():
    """Snapshot test: calcifer.__all__ matches the documented public API.

    On failure, the contributor must update __all__, this test's
    _EXPECTED_PUBLIC_API constant, AND docs/public-api.md in the
    same commit. Three coordinated edits = an intentional change.
    """
    import calcifer
    actual = set(calcifer.__all__)
    expected = set(_EXPECTED_PUBLIC_API)
    assert actual == expected, (
        f"calcifer.__all__ has drifted from the documented public API.\n"
        f"  Added (in __all__ but not expected):   "
        f"{sorted(actual - expected)}\n"
        f"  Removed (expected but not in __all__): "
        f"{sorted(expected - actual)}\n"
        f"Update _EXPECTED_PUBLIC_API in this test AND docs/public-api.md\n"
        f"in the same commit if the change is intentional."
    )


def test_public_api_importable():
    """Every name in __all__ must be a real, non-None attribute."""
    import calcifer
    for name in calcifer.__all__:
        obj = getattr(calcifer, name, None)
        assert obj is not None, (
            f"calcifer.__all__ lists {name!r} but it is missing or None"
        )


def test_public_api_documented_in_md():
    """Every public name must appear in docs/public-api.md.

    Closes the doc-stub footgun: a doc with just the three section
    headers ("Stable", "Provisional", "Internal") would pass the
    section grep but fails this content check, because none of the
    actual names appear in it.
    """
    import calcifer
    md_path = ROOT / "docs" / "public-api.md"
    assert md_path.exists(), f"docs/public-api.md missing at {md_path}"
    md_text = md_path.read_text(encoding="utf-8")

    missing = [name for name in calcifer.__all__ if name not in md_text]
    assert not missing, (
        f"docs/public-api.md does not mention {len(missing)} public name(s):\n"
        f"  {missing}\n"
        f"Every name in calcifer.__all__ must appear (case-sensitive) in "
        f"docs/public-api.md."
    )


# ────────────────────────────────────────────────────────────────────
# CHANGELOG + semver policy doc (sdk-changelog-semver)
# ────────────────────────────────────────────────────────────────────


def test_changelog_exists_and_has_v030_entry():
    """CHANGELOG.md must exist with a populated [0.3.0] section.

    Enforces: keep-a-changelog header reference, a `## [0.3.0]`
    heading, at least 5 bullet entries (`- `) inside the v0.3.0
    section, and at least 2 distinct `### ` category headings
    (e.g. Added + Changed). This matches the contract's AC.
    """
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.exists(), f"CHANGELOG.md missing at {changelog}"
    text = changelog.read_text(encoding="utf-8")

    lower = text.lower()
    assert "keepachangelog" in lower or "keep a changelog" in lower, (
        "CHANGELOG header should reference keep-a-changelog"
    )

    assert "## [0.3.0]" in text, "CHANGELOG missing '## [0.3.0]' section"

    # Slice out the v0.3.0 section: from its heading to the next `## [`
    start = text.index("## [0.3.0]")
    rest = text[start + len("## [0.3.0]"):]
    next_section = rest.find("\n## [")
    section = rest if next_section == -1 else rest[:next_section]

    bullet_lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(bullet_lines) >= 5, (
        f"v0.3.0 section has only {len(bullet_lines)} bullet entries; "
        f"contract requires at least 5"
    )

    categories = {
        ln.strip() for ln in section.splitlines() if ln.startswith("### ")
    }
    assert len(categories) >= 2, (
        f"v0.3.0 section has only {len(categories)} '### ' category "
        f"heading(s); contract requires at least 2 (e.g. Added + Changed). "
        f"Found: {sorted(categories)}"
    )


def test_semver_policy_doc_exists():
    """docs/semver.md must exist and reference public-api.md.

    Also asserts the doc enumerates Major/Minor/Patch triggers so a
    stub doc can't pass.
    """
    semver_md = ROOT / "docs" / "semver.md"
    assert semver_md.exists(), f"docs/semver.md missing at {semver_md}"
    text = semver_md.read_text(encoding="utf-8")

    assert "public-api.md" in text, (
        "docs/semver.md must reference docs/public-api.md "
        "(it's the source of public-API names)"
    )
    for trigger in ("Major", "Minor", "Patch"):
        assert trigger in text, (
            f"docs/semver.md missing version-bump trigger heading: {trigger}"
        )
