# Calcifer Semver Policy

Calcifer follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
with the following project-specific interpretations. This document is
the canonical source — `docs/public-api.md` lists *what* is public,
this file defines *how* changes to it are versioned.

## What is the public API?

The public API is exactly the set of names listed in
[`docs/public-api.md`](public-api.md) AND in `calcifer.__all__`. The
`tests/test_packaging.py::test_public_api_surface` snapshot test
ensures these two stay in sync — drift in either direction fails CI.

Anything else — submodules, helpers, internal symbols — has **no**
stability guarantee. Use at your own risk.

## What triggers each version bump

### Major (X.0.0)

- Removing or renaming any name in `docs/public-api.md`.
- Changing the type signature of any **Stable** name in a way that
  breaks existing call sites.
- Changing the runtime behavior of a Stable name in a way that
  existing tests would fail.

### Minor (0.X.0)

- Adding new public API names.
- Changing **Provisional** names (with a CHANGELOG entry).
- Deprecating Stable names (the deprecation warning must persist for
  at least one minor version before removal in the next major).
- New features that are additive.
- Performance improvements that don't change observable behavior.

### Patch (0.0.X)

- Bug fixes that don't change documented behavior.
- Documentation-only changes.
- Internal refactoring with no public API impact.
- Test additions.

## Deprecation policy

Before removing a Stable name in a major release:

1. Mark it deprecated in at least one preceding minor release.
2. The deprecation must emit a `DeprecationWarning` at runtime.
3. The CHANGELOG must list the deprecation under "Deprecated".
4. The replacement (if any) must be documented in the same release.

## Procedure for changing the public API

To add or remove a name from `__all__`, edit **three** files in the
same commit:

1. `calcifer/__init__.py` — the `__all__` list.
2. `docs/public-api.md` — the documentation.
3. `tests/test_packaging.py` — the `_EXPECTED_PUBLIC_API` constant.

The snapshot test `test_public_api_surface` will fail unless all
three are in sync. This is intentional — three coordinated edits
force the change to be deliberate and reviewable.
