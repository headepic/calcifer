#!/usr/bin/env bash
# Calcifer harness init script
# Verifies the environment is ready and the working tree is clean before a session starts.
#
# Exit codes:
#   0 = ready
#   1 = environment broken (venv missing, deps missing, tests fail)
#   2 = dirty working tree (uncommitted changes)

set -e
set -o pipefail

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

# 4. Working tree clean (including untracked files)
if [ -n "$(git status --porcelain)" ]; then
    echo ""
    echo "WARN: working tree has uncommitted changes (tracked or untracked):"
    git status -s
    echo ""
    echo "A session should start from a clean state. Commit, stash, or clean first."
    exit 2
fi
echo "OK: working tree clean"

# 5. Test suite (quick sanity check — mock tests only)
# Detect which timeout binary is available:
#   - Linux: GNU coreutils `timeout` is default
#   - macOS: neither is default; `gtimeout` exists if `brew install coreutils`
#   - Fallback: run without timeout (rely on user to Ctrl+C if stuck)
_TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
    _TIMEOUT_CMD="timeout 300"
elif command -v gtimeout >/dev/null 2>&1; then
    _TIMEOUT_CMD="gtimeout 300"
fi

echo ""
if [ -n "$_TIMEOUT_CMD" ]; then
    echo "==> Running mock test suite (timeout: 300s via $_TIMEOUT_CMD)..."
else
    echo "==> Running mock test suite (no timeout — install GNU coreutils for timeout enforcement)..."
fi

_PYTEST_LOG=$(mktemp)
trap 'rm -f "$_PYTEST_LOG"' EXIT INT TERM

if $_TIMEOUT_CMD .venv/bin/python -m pytest tests/ -x -q \
    --ignore=tests/test_e2e_real.py \
    --ignore=tests/test_e2e_mcp_skill.py \
    --ignore=tests/test_tui_web.py \
    > "$_PYTEST_LOG" 2>&1; then
    tail -3 "$_PYTEST_LOG"
    echo ""
    echo "OK: tests pass"
else
    _RC=$?
    tail -20 "$_PYTEST_LOG"
    echo ""
    if [ $_RC -eq 124 ]; then
        echo "FAIL: tests timed out after 300s"
    else
        echo "FAIL: tests failing (exit $_RC). Fix before starting a new session."
    fi
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
