#!/usr/bin/env bash
# Calcifer harness init script
# Verifies the environment is ready and the working tree is clean before a session starts.
#
# Exit codes:
#   0 = ready
#   1 = environment broken (venv missing, deps missing, tests fail)
#   2 = dirty working tree (uncommitted changes)

set -e

cd "$(dirname "$0")/.."

echo "==> Calcifer harness init"
echo ""

# 1. venv check
if [ ! -d ".venv" ]; then
    echo "FAIL: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    exit 1
fi
echo "OK: .venv exists"

# 2. Python version
PY_VERSION=$(.venv/bin/python --version 2>&1)
echo "OK: $PY_VERSION"

# 3. Calcifer importable
if ! .venv/bin/python -c "import calcifer" 2>/dev/null; then
    echo "FAIL: calcifer not importable. Run: .venv/bin/pip install -e '.[dev]'"
    exit 1
fi
echo "OK: calcifer importable"

# 4. Working tree clean
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    echo ""
    echo "WARN: working tree has uncommitted changes:"
    git status -s
    echo ""
    echo "A session should start from a clean state. Commit or stash first."
    exit 2
fi
echo "OK: working tree clean"

# 5. Test suite (quick sanity check — mock tests only)
echo ""
echo "==> Running mock test suite..."
if .venv/bin/python -m pytest tests/ -x -q \
    --ignore=tests/test_e2e_real.py \
    --ignore=tests/test_e2e_mcp_skill.py \
    --ignore=tests/test_tui_web.py \
    2>&1 | tail -3; then
    echo ""
    echo "OK: tests pass"
else
    echo "FAIL: tests failing. Fix before starting a new session."
    exit 1
fi

# 6. Git status
echo ""
echo "==> Git status"
echo "Branch: $(git branch --show-current)"
echo "HEAD:   $(git log --oneline -1)"

echo ""
echo "==> READY. Next steps:"
echo "    python harness/harness.py status"
echo "    python harness/harness.py pick"
