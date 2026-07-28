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
- As of STORY-010, `LLM_API_KEY` is real, implemented configuration
  (`app/core/config.py` — see [docs/AI_SAFETY.md](docs/AI_SAFETY.md)
  Section 4): a `SecretStr` field, masked in `repr()`/`str()`
  (`tests/core/test_config.py::test_llm_api_key_is_masked_in_repr`),
  optional at application startup (the app and every non-AI route work
  with none configured), and read from environment/`.env` only — never
  hardcoded, never committed. `.env.example` holds only placeholder
  values (`LLM_API_KEY=changeme`). `app.ai.providers.anthropic_provider.AnthropicProvider`
  never logs the key and never includes it in any exception message —
  every vendor SDK exception is caught and translated to a generic,
  safe error before it can reach a log line or an API response. No test
  in this codebase requires a real Anthropic API key or internet
  access — every test uses `app.ai.providers.fake_provider.FakeLLMProvider`
  instead of a real provider call.

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
- As of STORY-006, `Department` and `Practitioner` are also real,
  persisted models (`app/models/department.py`,
  `app/models/practitioner.py` — see
  [docs/SCHEDULING_RESOURCES.md](docs/SCHEDULING_RESOURCES.md)).
  `Practitioner` is administrative scheduling data only: no diagnosis
  capability, no treatment/prescription authority, and no unnecessary
  personal information (no email, phone, address). A practitioner's
  name is still real PII once populated with a real person's data — the
  synthetic-data requirement applies to it in full, same as `Patient`.
- As of STORY-007, `Appointment` is also a real, persisted model
  (`app/models/appointment.py` — see
  [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md)). It is deliberately
  administrative-only: `patient_id`/`practitioner_id`/`department_id`/
  `start_at`/`end_at`/`status`/`cancellation_reason` — no diagnosis,
  symptoms, medication, treatment, or clinical-note content exists
  anywhere on it, and none may be added without a fresh, explicit design
  decision. `cancellation_reason` specifically must remain a short
  ADMINISTRATIVE reason only (e.g. "patient requested") — never
  clinical content — see [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md)
  Section 10.
- As of STORY-008, `PatientDocument` is also a real, persisted model
  (`app/models/patient_document.py` — see
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md)). It holds document METADATA
  only — the uploaded file's actual bytes are never stored in
  PostgreSQL (no `BLOB`/`bytea` column exists), and no OCR, text
  extraction, or medical/clinical interpretation of a document's
  contents exists anywhere in this domain. Uploaded files are treated as
  UNTRUSTED INPUT: validated by file signature (a small PDF/JPEG/PNG
  allowlist), never executed/rendered/imported/deserialized, and never
  committed to this repository — see
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md) Section 9. **Signature
  validation is explicitly NOT malware scanning** — see
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md) Section 20 for the documented
  deployment-integration boundary this story does not claim to close.
- As of STORY-009, `WorkflowRun`/`WorkflowStep`/`WorkflowEvent` are also
  real, persisted models (`app/models/workflow.py` — see
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md)). They record administrative
  workflow STATE ONLY: request type, status, timestamps, a bounded
  `safe_metadata` field, and bounded `(failure_code,
  failure_message_safe)` failure metadata — no raw patient conversation/
  request text, no LLM prompt or response, and **no chain-of-thought or
  hidden model reasoning of any kind** exists anywhere on these models,
  and none may be added without a fresh, explicit design decision (see
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md) Sections 12, 15, and 23). No
  diagnosis, treatment, prescription, or urgency-triage category exists
  in `WorkflowRequestType`.
- As of STORY-010, the first LLM integration (`app/ai/` — see
  [docs/AI_SAFETY.md](docs/AI_SAFETY.md)) is real. The model is treated
  as fully UNTRUSTED throughout: it never receives, and cannot request,
  real patient PII/PHI beyond what a caller's own request text or a
  resolved tool argument (a UUID) already contains, and
  `AgentExecuteRequest.request_text` (the one free-form field in this
  story) is deliberately NEVER persisted anywhere in the workflow
  chain — proven directly in
  `tests/ai/test_orchestration.py::test_no_raw_request_text_is_ever_persisted`.
  No tool exposes a clinical-decision capability (diagnosis, treatment,
  prescription, dosage), and a deterministic, code-level safety policy
  (`app.ai.safety.SafetyPolicy`) refuses symptom-based or
  medication/dosage requests BEFORE the model is ever called — see
  [docs/AI_SAFETY.md](docs/AI_SAFETY.md) Section 7.
- As of STORY-011, this codebase implements genuine multi-agent
  coordination (`app/ai/agents/`, `app/ai/coordinator_decisions.py` —
  see [docs/AGENTS.md](docs/AGENTS.md)). The same untrusted-model
  posture applies to EVERY agent — Coordinator and each specialist —
  identically; the Coordinator additionally CANNOT execute any domain
  tool at all, a structural (schema-level) guarantee, not a runtime
  check. A handoff between agents carries only a specialist's stable
  name plus an optional bounded category string — never a
  Coordinator-composed prompt, never patient PII/PHI beyond what the
  original request text or a resolved tool argument already contained.
  No agent may change organization/user/role/patient-self-scope —
  proven by explicit adversarial tests (see
  [docs/AGENTS.md](docs/AGENTS.md) Section 7).

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
- As of STORY-006, the same applies to `Department`/`Practitioner`
  request/response contents (names in particular) —
  `app/api/v1/endpoints/departments.py`, `practitioners.py`, and their
  services do not log request/response bodies today.
- As of STORY-007, the same applies to `Appointment` request/response
  contents — `app/api/v1/endpoints/appointments.py`,
  `app/services/appointment.py`, and `app/services/availability_query.py`
  do not log request/response bodies today. This includes never logging
  a raw database exclusion-constraint violation's detail text (which
  echoes back the conflicting `patient_id`/`practitioner_id`/time range)
  — `AppointmentService` translates it into a generic
  `AppointmentConflictError` message before it ever reaches a log
  statement or an API response (see
  [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md) Section 7).
- As of STORY-008, the same applies to `PatientDocument`
  request/response contents (`original_filename` in particular) —
  `app/api/v1/endpoints/documents.py`, `app/services/document.py`, and
  `app/storage/` do not log request/response bodies or file contents
  today. Uploaded file BYTES are never logged under any circumstance —
  every operation on them is a stream, digest computation, or opaque
  storage write, never something serialized to a log line.
- As of STORY-009, the same applies to `WorkflowRun`/`WorkflowStep`/
  `WorkflowEvent` — `app/api/v1/endpoints/workflows.py` and
  `app/services/workflow.py` do not log request/response bodies today.
  `WorkflowService.fail_workflow`/`fail_step` accept only a pre-bounded
  `(failure_code, failure_message_safe)` pair; nothing in this codebase
  serializes a raw exception's `str()`/`repr()`, a stack trace, or a SQL
  statement into a persisted or logged field for these models — see
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md) Section 13.
- As of STORY-010, `app/api/v1/endpoints/agent.py` and `app/ai/`
  (providers, orchestration, tools) do not log request/response bodies
  today. Specifically never logged: `AgentExecuteRequest.request_text`,
  the system prompt, a provider's raw response, `LLM_API_KEY`, or any
  chain-of-thought/reasoning content (none exists to log — see
  [docs/AI_SAFETY.md](docs/AI_SAFETY.md) Section 6). Every vendor SDK
  exception `AnthropicProvider` catches is translated to a safe,
  generic message before it reaches any log statement or API response —
  never the vendor exception's own text, which could include request
  detail.
- As of STORY-011, the same applies to `app/ai/agents/` and
  `app/ai/coordinator_decisions.py`: never logged — a specialist's or
  the Coordinator's system prompt, a Coordinator's `task_category` free
  text, or any provider response from either the coordinator or
  specialist decision call. Only safe, bounded values ever reach
  persistence or logs: agent names (`"coordinator"`/`"scheduling"`/
  `"document"`/`"routing"`), tool names, and safe result/failure codes
  (e.g. `"forbidden_tool"`) — see [docs/AGENTS.md](docs/AGENTS.md)
  Section 6.

## 10. Medical Document Handling Considerations

- Any future feature that ingests, stores, or processes medical documents
  (referrals, forms, uploaded files) must treat that content as sensitive by
  default, even in a demo context.
- Uploaded/generated documents are excluded from version control
  (`uploads/`, `local_storage/` are gitignored) and must never be committed
  as fixtures unless they are clearly synthetic.
- **Implemented in STORY-008** (see
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md) and
  [docs/adr/ADR-0008-document-storage-and-security.md](docs/adr/ADR-0008-document-storage-and-security.md)):
  `PatientDocument` administrative document metadata, a storage
  abstraction (`app/storage/`) with a filesystem-backed LOCAL
  DEVELOPMENT ONLY implementation, signature-based upload validation
  (a small PDF/JPEG/PNG allowlist — never file extension or
  client-declared `Content-Type` alone), streaming size limits, SHA-256
  integrity hashing, opaque server-generated storage keys, and
  soft-deletion/retirement semantics. **Malware scanning is explicitly
  NOT implemented** — signature validation is not malware scanning; see
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md) Section 20 for the documented
  deployment-integration boundary that must be closed before any real
  (non-synthetic) document upload reaches production. A production
  object-storage backend (credentialed, e.g. S3-compatible) is also not
  yet implemented — `Settings` refuses to start the application with
  local document storage selected outside `development`/`test`.
- Test uploads use only synthetic, hand-constructed byte sequences
  (never real files) and are written exclusively to pytest-managed
  temporary directories — never `backend/local_storage/` (the real
  configured development path) and never committed to version control.

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
