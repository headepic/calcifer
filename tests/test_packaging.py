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
