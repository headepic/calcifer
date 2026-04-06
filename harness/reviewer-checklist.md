# Reviewer Checklist

This checklist is read at runtime by `harness.py review` and included in
every review packet. Keep it ordered by frequency of past misses (most
common first). Append new rules when a `harness.py review-miss` surfaces
a recurring failure mode the current checklist doesn't catch.

Every entry should cite the incident that motivated adding it, so the
provenance is visible in git history.

---

1. **Motivation**: is the problem real? Is urgency justified? Could it wait?
   Could it be solved by deleting code instead of adding?

2. **Reference**: does the "Claude Code Reference" section accurately
   describe the source OR honestly say "no direct analog"? Spot-check at
   least one line-number claim against the actual `claude-code-source`
   file. Historical miss: first-draft `mcp-auth-refresh` cited
   `client.ts:363-421` as "OAuth token handling" but it was actually the
   Anthropic-specific proxy fetch — cited file existed, its meaning did
   not match.

3. **Symbol already exists?** (The canonical pre-post-tool-hooks failure
   mode.) Does the contract propose adding a symbol, method, or file that
   **already exists** in the codebase? Grep the calcifer source for every
   new name mentioned in the Design section. Historical miss:
   `pre-post-tool-hooks` proposed adding `HookEvent.PRE_TOOL_USE` which
   was already defined at `calcifer/services/hooks.py:27`. The whole
   contract was rewritten.

4. **Scope**: are non-goals explicit and reasonable? Is it really ONE
   feature, or three features bundled? Rule of thumb: can the generator
   complete it in ~5 commits or fewer?

5. **Design matches reality**: does the design proposal match the
   current state of the code, not a stale mental model? Read every file
   mentioned in the Design section and verify at least one specific
   claim about it.

6. **Acceptance Criteria**: is EVERY item yes/no verifiable by someone
   who didn't write the feature? Are there ≥3 items? Do they cover at
   least one error path, not just the happy path?

7. **Verification Commands**: mentally simulate running them NOW, before
   implementation. Which ones fail? If ALL commands pass before
   implementation, the gate is broken — reject. Prefer import/attribute
   checks (`.venv/bin/python -c "from X import Y"`) over grep-on-source.
   Historical miss: round-1 grep-based gates passed trivially on
   comments and docstrings.

8. **Verification footguns**: could a lazy/broken implementation (no-op
   function, stub returning the expected type, docstring containing the
   right words) pass verify? If yes, the tests are too weak.

9. **Contract ↔ features.json sync**: does the contract's "Verification
   Commands" block match the features.json `verification` array
   verbatim? Drift here means the gate is lying about what it runs.

10. **Session scope**: can this really be done in ONE session? If it has
    more than ~5 commits worth of work, break it up into dependent
    features.

11. **Dependencies**: does this feature depend on another not-yet-done
    feature? If yes, is that feature listed and will it be done first?

12. **Rollback Plan**: is it concrete? Does it name a rollback path that
    doesn't corrupt features.json or progress.md?

---

## How to evolve this checklist

After a real review catches a bug the checklist didn't flag, or after an
approved contract turns out to be wrong during implementation:

1. Run `python harness/harness.py review-miss <feature-id> --what "<description>"`
2. Add a new numbered rule above that names the incident and the
   concrete thing to check.
3. Commit the checklist change with a short message referencing the miss.

This is the "calibration loop" from Article 2:

> "The tuning loop was to read the evaluator's logs, find examples where
> its judgment diverged from mine, and update the QA's prompt to solve
> for those issues."
