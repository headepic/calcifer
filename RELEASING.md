# Releasing Calcifer

This document is the end-to-end procedure for cutting a PyPI release.
It assumes `.github/workflows/publish.yml` is already in place (see
`sdk-github-actions-ci`) and the repo is green on CI.

Everything marked **⚠️ human only** has irreversible side effects
(a real tag push, a real PyPI upload). Do not automate those steps.

---

## 1. One-time setup (first release only)

PyPI uses OIDC trusted publishing, which means GitHub Actions
exchanges an OIDC token for a short-lived PyPI upload credential.
No long-lived API token is stored in this repo or in GitHub
Secrets.

To enable the PyPI trusted publisher for `calcifer`:

1. Sign in to https://pypi.org as the project owner.
2. Go to **Your projects** → **calcifer** → **Publishing** (or, if
   the name is not yet claimed, go to **Publishing** directly and
   use the "pending publisher" form).
3. Click **Add a new publisher** → **GitHub**. Fill in:
   - **PyPI project name**: `calcifer`
   - **Owner**: `headepic`
   - **Repository name**: `calcifer`
   - **Workflow name**: `publish.yml`
   - **Environment name**: *(leave blank)*
4. Save.

(Optional) Repeat the same form at https://test.pypi.org if you
want the TestPyPI dry-run path. If you do this, uncomment the
commented-out TestPyPI step in `.github/workflows/publish.yml`
for the rehearsal and re-comment it before the real release.

Nothing in the repo changes for this step — it is all UI on
pypi.org.

---

## 2. Per-release checklist

Using `0.3.0` as the running example. Substitute the real version.

1. **Confirm CI is green on `main`.** Check the CI badge at the
   top of `README.md` (or the Actions tab). No merging a release
   onto red.

2. **Update `CHANGELOG.md`.** Move any entries under
   `## [Unreleased]` into a new dated section:

   ```markdown
   ## [Unreleased]

   ## [0.3.0] - 2026-04-06
   ```

   Double-check that the version's `### Added` / `### Changed` /
   `### Fixed` subsections are accurate.

3. **Bump `pyproject.toml` version** from `0.3.0.dev0` to
   `0.3.0` (strip the `.devN` suffix):

   ```toml
   [project]
   version = "0.3.0"
   ```

4. **Commit and push** on `main`:

   ```
   git add pyproject.toml CHANGELOG.md
   git commit -m "release: v0.3.0"
   git push origin main
   ```

   Wait for CI to turn green on the release commit before tagging.

5. **⚠️ human only — Tag and push.** Signed tag preferred:

   ```
   git tag -s v0.3.0 -m "Release v0.3.0"
   git push origin v0.3.0
   ```

   The tag push triggers `.github/workflows/publish.yml`.

6. **Watch the publish run** at
   https://github.com/headepic/calcifer/actions/workflows/publish.yml.
   The `build-and-publish` job should complete in about a minute.
   If it fails, see § 4 **Rollback** below — do not delete the
   tag as a "fix."

---

## 3. Verifying the release

Once the `publish.yml` run succeeds, PyPI will show the new
version. Smoke-test it from a fresh virtualenv that has no
cached copy of calcifer:

```
python -m venv /tmp/calcifer-smoke
/tmp/calcifer-smoke/bin/pip install --no-cache-dir calcifer==0.3.0
/tmp/calcifer-smoke/bin/python -c "from calcifer import Agent; print(Agent.__doc__)"
```

Expected: the Agent class docstring prints without any
ImportError. If the import works but any public name is missing,
something went wrong between `__all__` and the wheel — file an
issue and start the rollback flow.

Also verify type hints ship:

```
/tmp/calcifer-smoke/bin/python -c "from pathlib import Path; import calcifer; assert (Path(calcifer.__file__).parent / 'py.typed').exists()"
```

---

## 4. Rollback

PyPI releases **cannot be un-published**. Once a wheel is up, it
stays in PyPI's history forever, even if you yank it. You have
two real options if something is wrong:

1. **Yank the bad release.** A yanked version is still
   downloadable by exact pin (`calcifer==0.3.0`) but is skipped
   by dependency resolvers for fuzzy requirements (`calcifer>=0.3`).
   This is the correct response to "ship broken, ship a fix
   immediately":

   ```
   # ⚠️ human only
   python -m twine yank calcifer 0.3.0 --reason "broken X, use 0.3.1"
   ```

   (Twine ≥ 5.0; requires the PyPI UI method if twine is older.)

2. **Publish a fix as the next patch version.** Bump to `0.3.1`,
   follow § 2 again. Do NOT try to re-upload `0.3.0`; PyPI will
   reject it with `File already exists`. There is no way to
   overwrite a published version.

**Deleting the git tag does NOT un-publish the wheel.** The tag
is only the trigger; the artifact is independent of the tag once
it reaches PyPI. If you delete the tag, the wheel is still live
and the only effect is that the next person to re-tag the same
SHA will trigger a duplicate-upload failure.

---

## 5. Post-release

Immediately after a release, open `main` again and bump to the
next dev version so the next commit stream is distinguishable
from the shipped release:

```
# pyproject.toml
version = "0.3.1.dev0"
```

And open a fresh `[Unreleased]` section at the top of
`CHANGELOG.md`:

```markdown
# Changelog

...

## [Unreleased]

## [0.3.0] - 2026-04-06
...
```

Commit:

```
git add pyproject.toml CHANGELOG.md
git commit -m "post-release: start 0.3.1.dev0"
git push origin main
```

From this point on, new changes accrue under `[Unreleased]` until
the next release reaches § 2 step 2.
