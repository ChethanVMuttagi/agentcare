# ADR-0008: Document Storage & Security

Status: Accepted

Date: 2026-07-27

## Context

STORY-008 introduces AgentCare's first capability that handles
untrusted, client-supplied BINARY content: administrative patient
document upload (identity, insurance, referral, consent scans and
similar). Every prior story's write path (`Patient`, `Appointment`,
scheduling resources) accepted only structured, typed, validated JSON —
this is the first time AgentCare accepts an arbitrary byte stream from a
client and must decide, deliberately, what "safe" means for that.

Central questions this story had to resolve: where do the file's bytes
actually live, given PostgreSQL is meant to remain the system of record
for structured data, not a blob store? How does a future production
deployment swap local disk for real object storage without rewriting
`PatientDocumentService`? What does "validate an uploaded file" mean
when the file is untrusted input — and specifically, how much can be
validated without either a fragile OS-level dependency or a false sense
of security? How does AgentCare guarantee a database row is never
`available` unless the object behind it genuinely exists, given
PostgreSQL and object storage don't share a transaction? What happens to
a document once someone wants it gone? And, explicitly: what is this
story NOT claiming about the safety of an uploaded file, so that claim
is never accidentally implied later?

## Decision

1. **PostgreSQL stores metadata and lifecycle state only. File bytes
   never enter the database** — no `BLOB`/`bytea` column exists on
   `PatientDocument`. This satisfies the requirement that core
   document/workflow data is persisted in SQL (queryable, joinable,
   transactional, backed up with the rest of the domain) while keeping
   large binary payloads in a storage system actually designed for them.
2. **A narrow storage `Protocol` (`app.storage.base.DocumentStorage`:
   `put`/`open_read_stream`/`delete`/`exists`) is the ONLY thing
   `PatientDocumentService` depends on** — never a concrete backend,
   never an SDK, never credentials. `LocalDocumentStorage`
   (`app/storage/local.py`), a filesystem-backed implementation, is the
   only one built in this story, explicitly scoped to LOCAL DEVELOPMENT
   ONLY.
3. **Storage keys are server-generated, opaque, and never
   client-influenced**: `f"{organization_id.hex}/{uuid4().hex}"` — never
   derived from patient name, email, date of birth, patient number, or
   the original filename, and never accepted as API input from any
   client.
4. **Untrusted-file validation is signature-based (magic bytes), for a
   deliberately small allowlist (PDF, JPEG, PNG), hand-rolled rather
   than a third-party library.** Neither file extension nor
   client-declared `Content-Type` is trusted for anything
   security-relevant.
5. **The maximum upload size is enforced DURING streaming, not after
   buffering** — a fixed-size chunked read loop
   (`app.services.document._validated_chunks`) tracks a running total and
   aborts the instant it exceeds `DOCUMENT_MAX_UPLOAD_BYTES`, never
   reading an unbounded amount into memory first.
6. **SHA-256 is computed incrementally over the same streamed chunks**
   and persisted as INTEGRITY metadata only — explicitly never used as
   an authentication or authorization mechanism.
7. **A deliberate three-phase upload state machine**
   (`pending -> available` or `pending -> failed`) is the sole mechanism
   reconciling PostgreSQL and object storage, which do not share a
   transaction. The `available_has_size_and_hash` database `CHECK`
   constraint makes "reported available without proof of a persisted
   object" a schema-level impossibility, not just a service-level
   promise.
8. **Deletion is soft (status transition to `deleted`), never hard.**
   Storage deletion is attempted BEFORE the status changes; if it fails,
   the row's status is left completely unchanged and a distinct error
   propagates — the service never reports successful deletion while the
   underlying object might still exist.
9. **Patients may upload and read their own documents, but may never
   delete/retire one, in this story** — a deliberately conservative RBAC
   policy; retirement is an ADMIN/STAFF-only, organizational decision.
10. **Malware scanning is explicitly deferred, and explicitly NOT
    conflated with signature validation.** No fake "always clean"
    scanner exists anywhere in this codebase. The upload state machine
    (Decision 7) is deliberately structured so a real scanning stage can
    be inserted between signature validation and the `available`
    transition in a future story, without redesigning the pipeline.

## Rationale

- **SQL metadata, object-storage bytes**: this is the standard, correct
  separation of concerns for binary content — a relational database is
  not the right tool for large blob storage (backup size, replication
  cost, buffer-cache pollution, no meaningful indexing on blob content),
  while the METADATA about a document (who, what, when, current state,
  integrity digest) is exactly the kind of structured, queryable,
  transactional data PostgreSQL already is this project's system of
  record for. This isn't a new principle for AgentCare — it's applying
  the project's existing "PostgreSQL is the system of record for
  structured domain data" stance (ADR-0002) to a data type that finally
  needed a boundary drawn.
- **A `Protocol`, not a concrete SDK dependency, in the service layer**:
  the story explicitly anticipates a production object-storage backend
  later. Coupling `PatientDocumentService` to (for example) `boto3`
  directly would mean every business-logic method also needs a policy
  for AWS-specific exceptions, credentials, and retry semantics baked
  in, and would make the LOCAL, credential-free development path
  (explicitly required for this story) awkward to express cleanly. A
  four-method `Protocol` is small enough to implement against, easy to
  fake in tests (see `tests/services/test_document.py`'s storage
  doubles), and exactly matches what business logic actually needs:
  "put these bytes somewhere retrievable, get them back, delete them,
  check existence" — nothing more.
- **Opaque, server-generated storage keys**: a storage key derived from
  (or even resembling) patient-identifying information would leak PII
  into a filesystem path, backup archive, storage-provider access log,
  or a future storage-bucket listing — none of which have the same
  access-control discipline as the application's own API and RBAC layer
  enforce. An opaque UUID-based key carries no information at all if it
  ever leaks through a lower-level channel the application doesn't fully
  control.
- **Hand-rolled magic-byte detection over `python-magic`/`libmagic`**:
  `python-magic` wraps the OS-level `libmagic` C library, which is
  notoriously inconsistent to install reliably across Windows and Linux
  CI images (missing shared library, version mismatches, needing a
  separate system package on Windows with no first-party wheel) — the
  story's own instructions explicitly called this risk out. For a
  three-format allowlist, the actual signatures (`%PDF-`, JPEG's
  `\xff\xd8\xff`, PNG's fixed 8-byte header) are public, stable, and
  trivial to check directly. A pure-Python, zero-dependency check is
  simpler to audit, has no supply-chain surface, and is exactly as
  reliable as a general-purpose library would be for a set this narrow
  — the general-purpose library's main value (auto-detecting hundreds of
  formats) isn't needed here at all.
- **Streaming size/hash enforcement**: reading an entire upload into
  memory before checking its size defeats the point of having a size
  limit at all — a sufficiently large upload could exhaust memory before
  the check ever runs. Enforcing the bound INSIDE the read loop, chunk
  by chunk, is the only way the limit is actually a limit. Computing the
  hash in the same pass costs nothing extra (the bytes are already being
  read) and avoids a second full read of the content.
- **The three-phase state machine, not "write to storage, then insert a
  row" or "insert a row, then write to storage" as an unguarded
  two-step**: either naive ordering has a silent-failure mode — write
  storage first and the DB insert fails: an orphaned object with no
  metadata reference, invisible and unmanageable; insert an `available`
  row first and the storage write fails: a phantom document a client
  could be told exists and then get a broken download. A `pending`
  intermediate state, committed durably BEFORE the storage write begins,
  means there is always a metadata trace of "this was attempted," and
  the final state transition only ever happens after the outcome is
  actually known — closing off both silent-failure modes at once, and
  giving operators something concrete to reconcile against (`pending`/
  `failed` rows older than some threshold are a legitimate
  operational-cleanup signal, PLANNED tooling this story does not build).
- **Soft deletion with storage-delete-before-status-change ordering**:
  the reverse ordering (mark deleted, then attempt to delete the object)
  has the same silent-failure shape as an unguarded two-step upload — a
  row reported deleted whose object still exists is a false success a
  caller has no way to detect or retry correctly. Attempting the storage
  operation FIRST, and only changing durable state once it's confirmed
  to have succeeded, is the same ordering principle applied to the
  opposite direction of the state machine.
- **Conservative patient-deletion policy**: administrative documents
  (identity verification, insurance, signed consent) often exist
  precisely so the ORGANIZATION has a durable record of something —
  letting the patient who submitted it unilaterally remove that record
  undermines the reason it was collected in the first place. Reserving
  deletion for `ADMIN`/`STAFF` is the safer default until a concrete
  requirement (e.g. a formal right-to-erasure workflow) justifies
  building something more elaborate.
- **Explicit malware-scanning deferral, not a fake scanner**: claiming a
  file is "safe" because it passed a three-line signature check would be
  actively misleading — a well-formed PDF can still carry a malicious
  payload. Building a scanner stub that always returns "clean" would be
  worse than building nothing, because it would look like a completed
  security control instead of an honest gap. Naming the gap explicitly,
  and shaping the state machine so a real scanner slots in without a
  redesign, is the honest version of "not implemented yet."

## Alternatives Considered

- **Storing file bytes directly in PostgreSQL (`bytea`/large objects)**:
  rejected — defeats the purpose of separating structured metadata from
  binary payload storage; bloats backups, replication, and buffer cache
  with content the relational engine has no special affinity for; the
  story explicitly ruled this out.
- **`python-magic` (or an equivalent `libmagic`-wrapping library)**:
  considered and rejected for this story's narrow three-format
  allowlist — see Rationale. Not rejected forever: if the allowlist grows
  to dozens of formats in a future story, revisiting this tradeoff would
  be reasonable.
- **A general-purpose pure-Python signature-detection library** (e.g.
  `filetype`/`puremagic`): considered — genuinely would have worked and
  avoided the `libmagic` risk. Declined in favor of hand-rolling
  specifically because the allowlist is only three formats; adding any
  dependency (even a pure-Python, well-maintained one) for three
  `startswith()` checks was judged unnecessary surface area for this
  story's scope. Worth reconsidering if the allowlist grows materially.
- **Coupling `PatientDocumentService` directly to a specific storage
  SDK** (e.g. `boto3`) now, "since production will need it eventually":
  rejected — this story explicitly excludes cloud credentials, and
  premature SDK coupling would make the credential-free local
  development path (a hard requirement) awkward, while providing no
  benefit until a real production deployment actually needs it.
- **A single combined upload transaction assuming storage and database
  can be made atomic together** (e.g. via a distributed transaction
  coordinator): rejected as significant infrastructure with no
  justification at this story's scope — the three-phase state machine
  achieves the same practical guarantee (no silently-orphaned state in
  either direction) with plain sequential operations and one additional
  database column set.
- **Allowing patients to delete their own documents**: considered (the
  story explicitly asked for a documented decision either way) and
  declined — see Rationale.
- **A stub/fake malware scanner returning "clean" unconditionally**:
  explicitly rejected — see Rationale; the story explicitly prohibited
  this.

## Consequences

- Every future story that needs to accept untrusted binary upload input
  should default to reusing `app.storage.base.DocumentStorage` (adding a
  new concrete backend if needed) and the same
  validate-before-persist / streaming-size-and-hash / state-machine
  pattern established here, rather than inventing a new upload pathway.
- The first story that adds a real production storage backend (e.g.
  S3-compatible) implements exactly the four `DocumentStorage` methods
  and wires it into `app.storage.factory.build_document_storage` — no
  change to `PatientDocumentService`, the API routes, or the database
  schema is expected.
- The first story that adds malware scanning inserts a new state-machine
  stage between signature validation and the `available` transition in
  `PatientDocumentService.upload_document` — the `pending` status and
  the `available_has_size_and_hash` constraint already accommodate a
  document that has passed structural validation but is not yet
  confirmed safe to serve.
- `Settings._forbid_local_document_storage_outside_development` means
  `staging`/`production` cannot start at all until a real storage
  backend is implemented and selected — this is a deliberate, visible
  blocker (matching the existing `JWT_SECRET_KEY` pattern), not
  something a future deployment should work around by relaxing the
  validator; the correct fix is building the real backend.
- If a future story needs a patient-initiated document-removal request
  (as opposed to unilateral deletion), that is a new, additive workflow
  (e.g. a request/approval state) layered on top of the existing
  ADMIN/STAFF-only `delete_document`, not a loosening of this ADR's RBAC
  decision.
