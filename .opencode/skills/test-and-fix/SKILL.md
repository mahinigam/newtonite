---
name: test-and-fix
description: Diagnose failing tests or regressions, make the smallest safe fix, and verify it without weakening tests or hiding failures.
compatibility: opencode
metadata:
  opencode/slash: "true"
---

# Test and Fix

## Objective

Find the root cause of a failing test, broken build, or reproducible regression; make the smallest correct change; and collect honest verification evidence. For the Newtonite work-management project, prioritize authorization, workflow, concurrency, idempotency, audit consistency, and pagination correctness.

Do not treat a green test run as the only goal. Preserve the intended product behaviour and existing user changes.

## Procedure

### 1. Establish context and protect the worktree

1. Read the repository's `AGENTS.md` and any relevant local instructions.
2. Inspect `git status --short` and the current diff before editing. Preserve all pre-existing user changes; do not overwrite or revert unrelated work.
3. Inspect the failing test/report, nearby implementation, project manifests/lockfiles, migrations, and test configuration.
4. Discover the project's actual commands and dependency manager from files such as `pyproject.toml`, `pytest.ini`, `package.json`, lockfiles, `Makefile`, or CI workflows. Do not assume commands or install dependencies until there is a reason.
5. If no failure has been reported, ask what should be tested or choose a clearly scoped verification task; do not make arbitrary code changes.

### 2. Reproduce before fixing

1. Run the narrowest relevant failing test or exact reported command first.
2. Capture the real failure, including assertion output, traceback, exit code, and relevant logs.
3. If the failure is not reproducible, repeat only when the test is expected to be flaky or timing-sensitive. Inspect environment/configuration differences before editing application code.
4. Classify the failure: implementation bug, incorrect/obsolete test expectation, fixture or test isolation bug, migration/schema issue, dependency mismatch, environment/configuration problem, or nondeterministic behaviour.
5. Trace the failure to a concrete cause. Do not make a speculative patch just to see whether the output changes.

### 3. Choose the smallest safe fix

1. Fix the underlying cause at the correct layer: API validation, authorization policy, application/service logic, database transaction/constraint, frontend cache/state, test fixture, or configuration.
2. Keep the change focused. Avoid unrelated refactors, broad rewrites, new dependencies, and architecture changes while diagnosing a narrow failure.
3. Add a regression test that would fail before the fix and pass after it, when the issue is an application bug and a suitable test is practical.
4. Do not make tests green by deleting assertions, skipping tests, loosening expectations without evidence, hiding errors, swallowing exceptions, adding arbitrary sleeps/retries, mocking away the behaviour under test, or disabling lint/type checks.
5. Do not change product rules just to satisfy an unexplained failure. If requirements conflict or expected behaviour is ambiguous, describe the alternatives and ask for a decision.

### 4. Apply Newtonite-specific correctness checks

When the failure touches the work-item domain, explicitly check the relevant invariants:

- **Authorization:** verify authenticated identity, team membership, role, and item scope on the server. Test both allowed and denied requests. Do not trust a client-supplied team ID.
- **Concurrency:** claims and conditional updates must be atomic at the database level. A read followed by an unconditional write is not an adequate race fix.
- **Stale updates:** ensure version/ETag checks happen as part of the write condition, and rejected writes do not alter state.
- **Idempotency:** ensure uniqueness is enforced in the database and concurrent duplicate requests cannot create multiple logical operations. Reusing a key for a different payload must be handled explicitly.
- **Audit consistency:** item mutation and activity-event insertion must share one transaction. If either fails, both should roll back.
- **Workflow:** validate transitions and role-specific approval/closure rules on the server. Reject invalid transitions without changing persisted state or writing a false success event.
- **Pagination/search:** ensure limits are bounded, filters run server-side, ordering is deterministic, and empty/edge pages behave correctly.
- **Background work:** if an outbox/worker exists, assume jobs can be retried or delivered more than once. Verify idempotent processing, bounded retries, and an observable terminal failure state.

For tests that claim to prove PostgreSQL transaction or concurrency semantics, use separate database sessions/connections against PostgreSQL. Do not replace such a test with a mock or SQLite and claim equivalent coverage.

### 5. Verify in increasing scope

Run checks in this order, using the project's documented commands:

1. The new or previously failing test.
2. The smallest related test module/suite.
3. The relevant backend or frontend test suite.
4. Configured formatter, lint, static type check, and build checks, where present and feasible.
5. Migration/schema checks or a fresh-start smoke test if database setup changed.
6. The broader test suite if the time budget permits.

After a failure, inspect the new output and decide whether it reveals the same root cause or a new one. Continue only with bounded, evidence-driven fixes. If two or three focused attempts do not clarify the cause, stop and report the evidence and the next useful diagnostic step instead of making increasingly broad changes.

Do not run a destructive database reset, remove volumes, delete data, `git reset --hard`, force checkout over edits, or overwrite environment files without explicit user approval and confirmation that the affected data is disposable.

### 6. Review the final diff

Before reporting completion:

- Review `git diff --check` and the full diff for unrelated changes.
- Confirm no secrets or real credentials were added.
- Confirm no test was skipped, deleted, weakened, or disabled as a shortcut.
- Confirm migrations and API schemas match the implementation.
- Update documentation only where behaviour or commands actually changed.
- Check that generated files, caches, build output, and temporary debug statements were not accidentally committed.

### 7. Report accurately

Return a compact report containing:

1. **Root cause:** what was actually wrong, backed by test/error evidence.
2. **Fix:** files/behaviour changed and why this is the appropriate layer.
3. **Regression coverage:** test(s) added or updated and the invariant they protect.
4. **Verification:** exact commands run and their real results. Distinguish passed, failed, and not run.
5. **Remaining risk:** any unverified behaviour, environment dependency, or recommended follow-up.

Never say tests pass unless they were run and passed in the current environment. If a check could not run because of a missing service, credentials, dependency, or unavailable environment, report that blocker plainly.

## Definition of done

A fix is complete only when the cause is understood, the minimal implementation change is made, relevant regression tests pass, relevant broader checks have been run where feasible, the diff is clean, and the report clearly separates verified facts from remaining uncertainty.
