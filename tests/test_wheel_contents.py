"""Tests that introspect the actual built wheel, not just the source tree.

The existing `test_py_typed_marker_present` in test_packaging.py checks
the editable install — but an editable install reads the source tree
directly, so a py.typed file on disk will always be found regardless
of whether the hatch build config actually ships it.

These tests close that gap by invoking `python -m build` and examining
the real wheel that would be uploaded to PyPI. They are gated on
`build` and `twine` being importable so a minimal dev environment can
still run the rest of the suite.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    """Build the calcifer wheel once per test module."""
    pytest.importorskip("build")
    out = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"python -m build failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly 1 wheel, got {wheels}"
    return wheels[0]


def test_wheel_can_be_built(built_wheel: Path):
    """Smoke test: the build fixture produced a non-empty wheel file."""
    assert built_wheel.exists()
    assert built_wheel.stat().st_size > 0
    assert built_wheel.suffix == ".whl"
    assert built_wheel.name.startswith("calcifer-"), built_wheel.name


def test_wheel_contains_py_typed(built_wheel: Path):
    """PEP 561: the py.typed marker must ship inside the built wheel.

    This closes the gap the existing test_py_typed_marker_present can't
    catch: an editable install reads the source tree directly, so
    py.typed will be found even if hatchling's build config silently
    drops it. Checking the wheel itself is the only way to prove the
    marker reaches end users.
    """
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    assert "calcifer/py.typed" in names, (
        f"calcifer/py.typed missing from built wheel {built_wheel.name}. "
        f"Hatchling must be configured to include it. "
        f"Wheel contents (first 20): {names[:20]}"
    )


def test_wheel_metadata_matches_pyproject(built_wheel: Path):
    """The wheel's METADATA file must match pyproject.toml's declared
    name, version, and key classifiers (Typing :: Typed, MIT license)."""
    # Parse the wheel METADATA file
    with zipfile.ZipFile(built_wheel) as zf:
        metadata_path = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")),
            None,
        )
        assert metadata_path is not None, (
            f"no *.dist-info/METADATA entry in wheel {built_wheel.name}"
        )
        raw = zf.read(metadata_path).decode("utf-8")

    msg = Parser().parsestr(raw)

    # Pyproject ground truth
    py = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected_version = py["project"]["version"]

    assert msg["Name"] == "calcifer", f"wheel Name = {msg['Name']!r}"
    assert msg["Version"] == expected_version, (
        f"wheel Version = {msg['Version']!r}, "
        f"pyproject version = {expected_version!r}"
    )

    classifiers = msg.get_all("Classifier") or []
    required = {
        "Typing :: Typed",
        "License :: OSI Approved :: MIT License",
    }
    missing = required - set(classifiers)
    assert not missing, (
        f"wheel METADATA missing classifiers: {missing}. "
        f"Present: {classifiers}"
    )


def test_twine_check_passes(built_wheel: Path):
    """`twine check` must pass on the built wheel (validates the README
    rendering, metadata shape, etc. — the same check PyPI runs)."""
    pytest.importorskip("twine")
    result = subprocess.run(
        [sys.executable, "-m", "twine", "check", str(built_wheel)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"twine check failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "PASSED" in result.stdout, (
        f"twine check output did not contain PASSED:\n{result.stdout}"
    )
