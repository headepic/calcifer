# Feature Contract: sdk-config-env-defaults

## Motivation

Calcifer's `CalciferConfig.base_url` defaults to `http://127.0.0.1:8317/v1`
and the same string is hardcoded as the default in 4 places (config.py:27,
agent.py:81, skills/executor.py:56, services/api/provider.py:74). This is
historical baggage from a private local LLM gateway used during development.

For an SDK published to PyPI, this default is **actively wrong**:

1. A user who runs `pip install calcifer` and then `Agent(api_key="...")`
   will silently try to connect to a local proxy that doesn't exist,
   getting a confusing connection error instead of "you forgot to set
   base_url" or "I'll use the OpenAI default".
2. The default leaks an internal artifact (port 8317) that has no meaning
   outside the original developer's machine.
3. Standard SDK convention is: read from environment variable, fall back
   to the canonical public endpoint (https://api.openai.com/v1).

This feature replaces the hardcoded default with env-driven resolution at
construction time. Users get sensible defaults; explicit values still win.

## Claude Code Reference

No direct analog. Claude Code is bound to Anthropic's API and reads its
own credentials via `ANTHROPIC_API_KEY` / `~/.config/anthropic/`. The
12-factor environment-variable pattern Calcifer adopts here is generic
SDK convention, not a Claude Code feature port.

For comparison, the OpenAI Python SDK uses `OPENAI_BASE_URL` and
`OPENAI_API_KEY` as its default env vars; we follow that convention so
calcifer drops in next to the OpenAI SDK without surprises.

## Scope

### 要做

- Change `CalciferConfig.base_url` default from `"http://127.0.0.1:8317/v1"`
  to `None` (Optional[str]).
- Add `_resolve_base_url()` helper that returns:
  1. `config.base_url` if explicitly set (truthy)
  2. else `os.environ["OPENAI_BASE_URL"]` if set
  3. else the canonical fallback `"https://api.openai.com/v1"`
- Apply the resolver in `Agent.__init__` before constructing `LLMProvider`,
  so by the time `_provider` is created, `self._config.base_url` holds the
  resolved value (not None).
- Remove the hardcoded `http://127.0.0.1:8317/v1` from:
  - `calcifer/config.py:27` (default → None)
  - `calcifer/agent.py:81` (kwarg default → None)
  - `calcifer/services/api/provider.py:74` (kwarg default → "https://api.openai.com/v1" — see non-goals for why)
  - `calcifer/skills/executor.py:56` (forked-skill agent default → None)
- Add 2 new tests in `tests/test_settings.py` (or a new dedicated file):
  - `test_config_base_url_explicit_wins`: explicit kwarg beats env var
  - `test_config_base_url_env_fallback`: env var beats canonical fallback
  - `test_config_base_url_canonical_fallback`: no kwarg, no env → openai.com
- Update existing tests that monkeypatch the old default if any exist.

### 不做 (non-goals)

- **NOT** changing `LLMProvider`'s constructor default in the same way.
  `LLMProvider` is a low-level transport class that should never be called
  with `base_url=None` — if it is, that's a programming error and should
  fail loudly. The resolver lives in the Agent layer (the public SDK
  surface), not in the provider layer. We DO change the provider's
  hardcoded literal to the canonical fallback `https://api.openai.com/v1`
  so a user who instantiates `LLMProvider` directly without args at
  least talks to a real endpoint instead of localhost:8317.
- Not adding `OPENAI_API_KEY` env reading. That's a separate feature
  (the existing code already does `os.environ.get("ANTHROPIC_API_KEY") or
  os.environ.get("OPENAI_API_KEY")` in cli.py for the CLI; pulling that
  into the library layer is out of scope).
- Not adding env var support for OTHER config fields (model, max_tokens,
  etc.). Future feature if requested.
- Not changing the test files that hardcode the old localhost URL —
  those are e2e tests that need a specific test gateway. They're
  intentionally pinned to the dev environment.
- Not changing `examples/e2e_test.py` — examples are aspirational and
  the user can read the new defaults.

## Design

### Files changed

1. **`calcifer/config.py`** — `CalciferConfig.base_url: str | None = None`
2. **`calcifer/agent.py`** — kwarg default `None`, call `_resolve_base_url()` after constructing `_config`
3. **`calcifer/services/api/provider.py`** — kwarg default `"https://api.openai.com/v1"` (constructor only; this is the literal that's used if someone instantiates `LLMProvider` directly without args)
4. **`calcifer/skills/executor.py`** — kwarg default `None`, runs through Agent which then resolves
5. **`tests/test_settings.py`** — 3 new tests for the resolver

### Resolver location

The resolver is a small private function on `Agent` (not a public API):

```python
import os

_OPENAI_FALLBACK_BASE_URL = "https://api.openai.com/v1"

def _resolve_base_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.environ.get("OPENAI_BASE_URL") or _OPENAI_FALLBACK_BASE_URL
```

Called in `Agent.__init__` after the `CalciferConfig` is built, BEFORE
the `LLMProvider` is constructed. The resolved value is written back to
`self._config.base_url` so any later code reading the config sees the
resolved value, not None.

### Backward compatibility

- Existing code that does `Agent(base_url="http://...")` continues to work
  (explicit value wins).
- Existing code that does `Agent(api_key="sk-...")` and relied on the
  127.0.0.1:8317 default **breaks** — it will now go to api.openai.com.
  This is a deliberate breaking change because the old default was wrong
  for any non-author user. The CHANGELOG entry will document it.
- E2E tests in `tests/test_e2e_*.py` and `examples/e2e_test.py` keep
  their explicit `base_url=` arguments because they need to talk to a
  specific gateway.

## Acceptance Criteria

- [ ] `CalciferConfig.base_url` field annotation is `str | None` with default `None`
- [ ] `Agent.__init__` `base_url` kwarg default is `None`
- [ ] After `Agent.__init__` returns, `agent._config.base_url` is the RESOLVED string (never None)
- [ ] When neither kwarg nor env is set, `agent._config.base_url == "https://api.openai.com/v1"`
- [ ] When `OPENAI_BASE_URL` env is set and kwarg is None, `agent._config.base_url` matches the env var
- [ ] When kwarg is explicit (truthy), it wins over env (env is ignored)
- [ ] No `127.0.0.1:8317` literal remains in `calcifer/config.py`, `calcifer/agent.py`, `calcifer/skills/executor.py`, or `calcifer/services/api/provider.py`
- [ ] New tests `test_config_base_url_explicit_wins`, `test_config_base_url_env_fallback`, `test_config_base_url_canonical_fallback` exist and pass
- [ ] All 460 existing mock tests still pass
- [ ] LLMProvider's hardcoded default is `"https://api.openai.com/v1"` (not localhost), so a direct instantiation with no args at least targets a real endpoint

## Verification Commands

```
.venv/bin/python -c "from calcifer.config import CalciferConfig; import dataclasses; f = next(x for x in dataclasses.fields(CalciferConfig) if x.name == 'base_url'); assert f.default is None, f'base_url default should be None, got {f.default!r}'"
.venv/bin/python -c "import subprocess; r = subprocess.run(['grep', '-rn', '127.0.0.1:8317', 'calcifer/'], capture_output=True, text=True); assert r.returncode != 0 or not r.stdout.strip(), f'127.0.0.1:8317 still in calcifer/: {r.stdout}'"
.venv/bin/python -m pytest tests/ -q -k 'config_base_url_explicit_wins or config_base_url_env_fallback or config_base_url_canonical_fallback'
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_e2e_real.py --ignore=tests/test_e2e_mcp_skill.py --ignore=tests/test_tui_web.py
```

Must match `features.json` verification array verbatim.

## Rollback Plan

If the env-driven default breaks existing user code unexpectedly (e.g.,
silently routing tests against api.openai.com and racking up bills),
the rollback is to keep `base_url: str = ""` and have the resolver
treat empty string as "not set" but require the caller to explicitly
opt into env reading via a new config flag. That preserves the old
behavior of "no default network access" while still removing the
localhost literal.

If the breaking change proves too aggressive in practice, alternative
fallback chain: explicit > env > raise. No silent default to
api.openai.com — caller must always say where to connect. This is
strictly safer but slightly less convenient than the OpenAI SDK
convention.

`git revert <commit>` is trivial — no schema changes, no files moved.
