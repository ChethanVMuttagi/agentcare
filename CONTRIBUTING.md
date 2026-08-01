# Contributing to AgentCare

AgentCare is developed **story by story**. This document describes how
work is scoped, branched, reviewed, and considered done.

## Story-Based Development

- Work is organized into discrete, narrowly scoped stories (e.g.
  `STORY-000`, `STORY-001`, ...). Each story has a clear goal and explicit
  boundaries of what it does and does not include.
- Do not implement work that belongs to a future story just because it
  seems convenient — scope creep across story boundaries makes review and
  history harder to follow.
- If a story reveals that a decision has broader architectural
  consequences, capture it as an ADR (see [docs/adr/](docs/adr/README.md))
  rather than quietly expanding the story's scope.

## Branch Expectations

- Branch from `main`.
- Name branches after the story or fix they implement, e.g.
  `story-001-fastapi-skeleton` or `fix/env-example-typo`.
- Keep branches scoped to a single story or fix — avoid bundling unrelated
  changes.

## Definition of Done

A change is **DONE** only when all of the following are true:

- **Implemented** — the functionality described by the story exists.
- **Tested** — relevant automated tests exist and pass locally.
- **Integrated** — the change works with the rest of the system, not just
  in isolation.
- **Security Checked** — reviewed against [SECURITY.md](SECURITY.md): no
  secrets, no real patient data, no unsafe logging, no new unreviewed
  attack surface.
- **Documented** — relevant docs in [docs/](docs/README.md) are added or
  updated; ADRs are written for architecturally significant decisions.
- **CI Passing** — every check in [`.github/workflows/`](.github/workflows/)
  is green: backend lint/type-check/tests (with coverage), frontend
  lint/type-check/unit tests (with coverage)/build, end-to-end (Playwright)
  tests, dependency/secret scanning, and CodeQL analysis.

Implemented + Tested + Integrated + Security Checked + Documented + CI
Passing = **DONE**. A change missing any of these is not done, regardless
of how complete the code looks.

## Tests Required

- New functionality includes tests appropriate to its layer: backend unit/
  integration tests (`backend/tests/`), frontend unit tests (`frontend/**/*.test.ts(x)`,
  run via `npm test`/`npm run test:coverage`), and, for a change that
  touches a user-facing flow, a Playwright end-to-end test
  (`frontend/e2e/`) covering it.
- Bug fixes include a test that reproduces the bug and passes once fixed,
  where practical.
- Tests use synthetic data only — see [SECURITY.md](SECURITY.md) Section 7.
- Coverage thresholds (`pyproject.toml`'s `[tool.coverage.report]` for the
  backend, `vitest.config.mts`'s `test.coverage.thresholds` for the
  frontend) must not regress.

## Documentation Required

- If a change affects behavior described in `docs/`, update the
  corresponding document in the same change, not as a follow-up.
- If a change introduces a new architecturally significant decision, add an
  ADR under [docs/adr/](docs/adr/README.md).
- Do not create documentation for functionality that doesn't exist yet.

## Security Review Required

- Every change is reviewed against [SECURITY.md](SECURITY.md) before merge.
- Confirm no secrets, credentials, tokens, or real patient data are present
  in the diff — including in test fixtures, comments, and commit messages.
- Confirm no new logging of sensitive data was introduced.

## No Secrets / PII

- Never commit `.env` files, API keys, credentials, or tokens.
- Never commit real patient data or PII/PHI of any kind, in any form
  (code, fixtures, screenshots, issue text, commit messages).
- If you're unsure whether something is sensitive, treat it as sensitive
  and ask before committing.

## Commit Expectations

- Write commit messages that explain *why*, not just *what*.
- Keep commits scoped and reviewable; avoid unrelated changes in the same
  commit.
- Reference the story or issue a commit relates to where applicable.

## Code Review Expectations

- All changes are reviewed before merging to `main`.
- Reviewers check the Definition of Done above, not just whether the code
  runs.
- Reviewers are expected to push back on scope creep beyond the story being
  implemented.
- Use the [pull request template](.github/pull_request_template.md), which
  includes explicit security and healthcare-safety checklist items.
