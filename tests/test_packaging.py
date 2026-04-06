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
