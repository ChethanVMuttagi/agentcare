# Security Policy

AgentCare is a **public repository**. Everyone — contributors, reviewers, and
anyone who forks or clones it — is expected to follow this policy. It is
written for a project in its foundational stage: some sections describe
policy that will be enforced by tooling in later stories (e.g., CI secret
scanning), not capability that exists today.

AgentCare is being built for the AgentCare Build Challenge 2026 as a
production-oriented SaaS foundation. It is a healthcare **administration and
care-coordination** system. See the [README](README.md) for the explicit
statement that AgentCare is not a diagnosis or treatment system.

## 1. Secret Management Policy

- Secrets (API keys, database credentials, JWT signing keys, tokens of any
  kind) must never be committed to this repository, in any branch, commit,
  issue, pull request description, or code comment.
- Secrets are provided to running applications exclusively through
  environment variables, loaded from a local, untracked `.env` file in
  development, or from **GitHub Secrets** / the deployment platform's secret
  store in CI/CD and production.
- No secret is ever hardcoded in application source code.

## 2. `.env` Policy

- `.env.example` is safe to commit. It contains placeholder values only and
  documents every configuration variable the application expects.
- `.env` is never committed. It is excluded via `.gitignore`
  (`.env` and `.env.*`, with an explicit exception for `.env.example`).
- Contributors create their own local `.env` by copying `.env.example` and
  filling in real values that stay on their machine only.

## 3. GitHub Secrets Policy

- CI/CD pipelines (introduced in a later story) will read secrets from
  GitHub Actions Secrets (repository or environment scoped), never from
  files committed to the repo.
- Secrets are scoped to the narrowest environment that needs them (e.g., a
  `staging` secret is not also exposed to a `demo` workflow unless required).
- Secrets are rotated whenever a contributor with access leaves the project
  or whenever exposure is suspected (see Section 11).

## 4. API Key Handling

- LLM provider keys (Groq, OpenAI, Anthropic, etc.) and any other
  third-party API keys are treated as secrets under Section 1.
- Keys are never logged, printed to console/stdout, included in error
  messages returned to clients, or embedded in frontend/client-side code.
- Each environment (local, staging, production) uses its own keys where the
  provider supports it, so a leaked dev key does not compromise production.

## 5. Database Credential Handling

- Database connection strings are supplied via `DATABASE_URL` (or
  equivalent) as an environment variable, never hardcoded.
- Local development database credentials are non-production, low-value
  placeholders (e.g., `changeme`) — they exist only for a database that
  contains synthetic data.
- Production database credentials are managed through the deployment
  platform's secret store and are never shared over chat, email, or
  committed anywhere.

## 6. Password & Authentication Token Handling

- Passwords are hashed with Argon2id (`argon2-cffi`) before storage —
  never with a hand-rolled hash, never with a fast general-purpose hash
  (MD5/SHA-family alone), and never stored in plaintext anywhere,
  including logs, error messages, or database backups.
- `User.password_hash` is never included in any API response — see
  [docs/RBAC.md](docs/RBAC.md).
- JWT access tokens are signed with `JWT_SECRET_KEY` (`SecretStr` in
  `Settings`, never logged, never returned by any endpoint).
  `JWT_SECRET_KEY` must be explicitly configured in `staging`/
  `production`; the application refuses to start otherwise rather than
  silently signing tokens with an insecure default.
- Tokens carry only a user identifier and standard claims (`sub`, `iat`,
  `exp`, `jti`) — never a password hash, role, permissions, or
  patient/medical data. See [docs/RBAC.md](docs/RBAC.md) for the full
  token-content and trust-boundary rationale, including the current
  token-revocation limitation (tokens are stateless and cannot be
  individually invalidated before expiry in this story).
- Login failure responses are intentionally generic and do not reveal
  whether a given email address has an account.

## 7. No Real Patient Data

- This is a demo/development/hackathon-originated repository. **Real patient
  data, real PHI (Protected Health Information), or any real individual's
  health information must never be introduced** into this repository, its
  issues, its pull requests, its test fixtures, or any environment tied to
  it.
- This applies to all environments reachable from this codebase, including
  local development and any public demo deployment.
- As of STORY-005, `Patient` is a real, persisted model
  (`app/models/patient.py` — see [docs/PATIENTS.md](docs/PATIENTS.md)).
  It is deliberately **administrative only**: no diagnosis, symptoms,
  medication, treatment, clinical notes, insurance, or emergency-triage
  content exists anywhere in the model, and none may be added to it
  without a fresh, explicit design decision (see
  [docs/adr/ADR-0005-patient-identity-and-access.md](docs/adr/ADR-0005-patient-identity-and-access.md)).
  Even the administrative fields it does have (name, date of birth,
  organization-assigned patient number) are real PII once populated with
  a real person's data — the synthetic-data requirement below applies to
  them in full.

## 8. Synthetic / Anonymized Test Data Requirement

- All test fixtures, seed data, and demo data must be synthetic
  (fabricated) or, if derived from real-world patterns, fully anonymized
  such that no real individual can be identified.
- Synthetic data should still be treated with care in structure (e.g.,
  realistic-looking identifiers) but must never map back to a real person.

## 9. Sensitive Logging Restrictions

- Application logs must never contain: API keys, JWT tokens, passwords,
  database credentials, or patient-identifying information.
- Request/response logging must redact or omit fields that could contain
  sensitive data (auth headers, tokens, free-text medical content) once
  logging is implemented.
- Debug logging that dumps full request bodies or environment variables is
  not permitted in code paths that could run against real data.
- As of STORY-005, this explicitly includes never logging `PatientCreate`
  request payloads or `PatientResponse`/`Patient` model contents (name,
  date of birth, patient number) — `app/api/v1/endpoints/patients.py` and
  `app/services/patient.py` do not log request/response bodies today, and
  any future logging added to this path must continue to exclude them.

## 10. Medical Document Handling Considerations

- Any future feature that ingests, stores, or processes medical documents
  (referrals, forms, uploaded files) must treat that content as sensitive by
  default, even in a demo context.
- Uploaded/generated documents are excluded from version control
  (`uploads/`, `local_storage/` are gitignored) and must never be committed
  as fixtures unless they are clearly synthetic.
- Document storage, access control, and retention are open design questions
  to be addressed in a dedicated ADR before implementation, not assumed.

## 11. If a Secret Is Accidentally Committed

If a secret, credential, or token is ever committed to this repository
(even briefly, even in a branch that's later deleted):

1. **Revoke and rotate the credential immediately**, at the provider (Groq,
   OpenAI, Anthropic, database host, etc.). Do this first, before anything
   else — assume the secret is compromised the moment it hits Git history.
2. **Do not assume deleting the file in a later commit is enough.** Git
   history retains the old content indefinitely and is publicly fetchable
   the moment it's pushed to a public repo. A follow-up commit that removes
   the file does not remove it from history.
3. **Clean Git history** where required, using tools such as
   `git filter-repo` or the BFG Repo-Cleaner, then force-push the rewritten
   history and ensure all clones/forks are aware history changed.
4. Notify the project maintainer(s) so the incident can be tracked.
5. Confirm the credential is truly dead (e.g., a rotated API key returns
   401/403) before considering the incident closed.

Rotation is the actual fix. History cleanup is hygiene that happens
afterward, not a substitute for rotation.

## 12. Responsible Vulnerability Reporting

If you discover a security vulnerability in AgentCare:

- **Do not open a public GitHub issue for it.**
- Report it privately to the maintainer. (A dedicated security contact
  email / GitHub Security Advisory process will be published here as the
  project matures; until then, contact the repository owner directly
  through GitHub.)
- Include enough detail to reproduce the issue and, if known, its potential
  impact.
- Please allow reasonable time for a fix before any public disclosure.

## 13. Public Repository Security Expectations

Because this repository is public:

- Assume everything committed is permanently visible, indexed, and
  scrapeable, even after deletion (see Section 11).
- Contributors must review their own diffs before pushing, specifically
  checking for secrets, credentials, and PII/PHI.
- No compliance certifications (HIPAA, SOC 2, or otherwise) are claimed by
  this project. It is engineered with security-conscious practices as a
  foundation, but achieving and certifying regulatory compliance is a
  separate, much larger effort that has not been undertaken.
- Security posture will be extended over time (dependency scanning, secret
  scanning in CI, etc.) as later stories introduce CI/CD — this document
  will be updated alongside that work rather than promising it in advance.
