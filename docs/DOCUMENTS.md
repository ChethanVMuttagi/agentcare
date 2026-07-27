# AgentCare Secure Document Management

This document describes the administrative patient-document collection
capability implemented in STORY-008: `PatientDocument` (metadata +
storage reference), `app.storage` (the object-storage abstraction),
`app.services.document_validation` (untrusted-upload validation), and
`PatientDocumentService` (upload orchestration, lifecycle, and safe
retrieval). It follows the same CURRENT vs. PLANNED discipline as
[ARCHITECTURE.md](ARCHITECTURE.md): everything described here as
implemented exists in the repository today; anything marked PLANNED
does not yet. See
[adr/ADR-0008-document-storage-and-security.md](adr/ADR-0008-document-storage-and-security.md)
for the decision record.

## 1. Administrative Scope (Healthcare Safety Boundary)

`PatientDocument` is an ADMINISTRATIVE record: metadata ABOUT an
uploaded file (who, what kind, how big, where it lives in storage,
whether it's currently retrievable). It never holds the file's
INTERPRETED content — no OCR, no text extraction, no summarization, no
medical/clinical interpretation of what a document contains exists
anywhere in this domain, and none may be added without a fresh,
explicit design decision.

`document_type = "referral"` means a referral document is stored
administratively (e.g. a PDF a referring provider sent) — it does
**not** mean AgentCare interprets, routes, or acts on the referral's
CONTENT clinically. This mirrors
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 14's
"administrative routing is not diagnosis" boundary, applied to documents.

Uploaded files are **UNTRUSTED INPUT**. This story never executes,
renders, imports, evaluates, or deserializes uploaded content in any
way — every operation on an uploaded file's bytes is either "compute a
SHA-256 digest," "check its first few bytes against a known signature,"
or "store/retrieve/delete it as an opaque blob."

## 2. SQL Metadata vs. Object Bytes

**PostgreSQL stores document METADATA and STATE — never the uploaded
file's bytes.** This satisfies the challenge requirement that core
document/workflow data is persisted in SQL, while keeping large binary
payloads out of the relational database entirely (no `BLOB`/`bytea`
column exists on `PatientDocument` — verified directly against the
schema, see Section 12). File bytes live in object storage, addressed
by an opaque `storage_key` metadata column — see Section 4.

## 3. The `PatientDocument` Model

`app/models/patient_document.py` — table `patient_documents`.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `patient_id` | UUID | NOT NULL, indexed — see Section 5 for ownership FK |
| `uploaded_by_user_id` | UUID | NOT NULL, indexed — see Section 5 |
| `document_type` | VARCHAR(16) | NOT NULL, CHECK constrained to `DocumentType` |
| `status` | VARCHAR(16) | NOT NULL, CHECK constrained to `DocumentStatus`, indexed |
| `original_filename` | VARCHAR(255) | NOT NULL — sanitized DISPLAY metadata only, see Section 8 |
| `storage_key` | VARCHAR(255) | NOT NULL, UNIQUE — opaque, server-generated, see Section 7 |
| `media_type` | VARCHAR(32) | NOT NULL, CHECK constrained to `DocumentMediaType` |
| `size_bytes` | BIGINT | NULLABLE — populated only once the object write succeeds |
| `sha256` | VARCHAR(64) | NULLABLE — populated only once the object write succeeds |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL |

`DocumentType` (`enum.StrEnum`): `identity`, `insurance`, `referral`,
`consent`, `other` — a deliberately small, administrative allowlist, the
same controlled-evolution rationale as every other enum in this
codebase (see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) "Enum Strategy").

`DocumentMediaType` (`enum.StrEnum`): `application/pdf`, `image/jpeg`,
`image/png` — the canonical MIME strings, reused directly as the
`Content-Type` on download (Section 13).

## 4. Document Status & Upload State Machine

`DocumentStatus` (`enum.StrEnum`): `pending`, `available`, `failed`,
`deleted`. Reflects whether the database record currently has a
successfully persisted object behind it — a document is **never**
reported as `available` before the storage write actually succeeds.

PostgreSQL and object storage do not share one transaction — there is
no way to atomically "write the file and the metadata row" as a single
operation. `PatientDocumentService.upload_document` implements a
deliberate state machine instead:

1. **Validate** tenant/patient/uploader (Section 5) and the upload's
   file-signature (Section 9). If ANY of this fails, **nothing is
   persisted anywhere** — no database row, no storage object. This is
   plain input-validation rejection (422/413), not a lifecycle failure.
2. **Create the row as `pending` and COMMIT it**, before any risky I/O
   begins — this durably records "an upload was accepted and object
   persistence is in progress." If the process crashes right here, the
   `pending` row is an auditable trace of what was in flight, not
   silence.
3. **Stream the bytes to storage**, computing the SHA-256 digest and
   enforcing the size limit as they pass through (Section 10).
   - **Success**: update the row to `available` with the now-known
     `size_bytes`/`sha256`, commit.
   - **Failure** (oversized upload detected mid-stream, or a genuine
     storage I/O error): best-effort delete whatever storage wrote so
     far, mark the row `failed`, commit, and re-raise the original
     error to the caller.

An `available` row pointing at a missing/incomplete object is not just
a service-level promise — it is a DATABASE-level impossibility, via the
`available_has_size_and_hash` `CHECK` constraint (`status <> 'available'
OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)`, see Section 12).

| Status | Meaning |
|---|---|
| `pending` | Accepted; object persistence in progress or unconfirmed. Never retrievable. |
| `available` | Object write succeeded; `size_bytes`/`sha256` populated. Safe to download. |
| `failed` | Object write did not complete. Row is KEPT (auditability). Never retrievable. |
| `deleted` | Retired — see Section 14. Never retrievable. No hard deletion of the row. |

## 5. Tenant & Patient Ownership

Enforced at the DATABASE level, the same composite-FK technique used
throughout this codebase (see `app/models/appointment.py`):
`(organization_id, patient_id) -> patients(organization_id, id)`.

**Uploader membership**: `(organization_id, uploaded_by_user_id) ->
organization_memberships(organization_id, user_id)` — a composite FK
guaranteeing `uploaded_by_user_id` was (at some point) a genuine member
of `organization_id`. This is a DATABASE-level guarantee. It does
**not**, and cannot, guarantee the membership was ACTIVE at the time of
this specific upload — `OrganizationMembership.is_active`/`role` are
mutable independently of any document row, the exact same
existence-vs-activity split already established for
`PractitionerDepartment.is_active` and `Patient` linkage validity (see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) and
[adr/ADR-0005-patient-identity-and-access.md](adr/ADR-0005-patient-identity-and-access.md)).
`PatientDocumentService` re-verifies the uploader has an ACTIVE
membership on every upload — a deliberate service-level check, not
assumed from the database constraint alone.

## 6. Storage Abstraction

`app/storage/base.py` defines `DocumentStorage`, a `typing.Protocol`
with exactly four operations: `put`, `open_read_stream`, `delete`,
`exists` — every one keyed by an opaque `storage_key`, never a filename
or patient identity. `PatientDocumentService` depends on this interface
only; it never imports a concrete storage backend or an SDK.

### Local Development Storage

`app/storage/local.py` — `LocalDocumentStorage`, a filesystem-backed
implementation. **LOCAL DEVELOPMENT ONLY.** `Settings` refuses to start
the application with `DOCUMENT_STORAGE_BACKEND=local` when `APP_ENV` is
`staging`/`production` (see Section 15) — local disk storage is not
durable and not shared across instances, inappropriate for a real
deployment.

- Every path is resolved relative to a single configured root
  (`DOCUMENT_STORAGE_PATH`) and defensively re-validated on every call:
  `.resolve()` normalizes `..` components, an absolute-path override
  attempt, and symlinks, then `is_relative_to(root)` confirms the result
  is still strictly inside the root — rejecting anything else with
  `StorageKeyInvalidError`, regardless of how the escape was attempted.
- `put()` writes to a temporary file in the SAME directory as the final
  target, then `os.replace()`s it into place — atomic on every platform
  this project supports (same-filesystem rename). A reader can never
  observe a partially-written object. On any failure (including the
  caller's own chunk iterator raising, e.g. an oversized upload), the
  temp file is removed and the original exception propagates unchanged.
- `delete()` is idempotent — a no-op, not an error, for a key that was
  never written or already deleted.
- The storage root (`local_storage/documents` by default, resolved
  relative to the backend's working directory) is covered by this
  repository's existing `local_storage/` `.gitignore` rule — never a
  tracked path.

### Future Production Storage (PLANNED)

An S3-compatible `DocumentStorage` implementation is the expected
production direction — implementing the SAME four-method interface, so
`PatientDocumentService` requires zero changes when it's added. No cloud
credentials, SDK, or S3-specific code exists anywhere in this story —
see [adr/ADR-0008-document-storage-and-security.md](adr/ADR-0008-document-storage-and-security.md).

## 7. Storage Key

Generated server-side, always opaque:
`f"{organization_id.hex}/{uuid.uuid4().hex}"`
(`app.services.document._generate_storage_key`). Never derived from a
patient's name, email, date of birth, patient number, or the original
filename — the `organization_id` component exists purely for
operational sharding/inspection convenience and is never exposed
through any public API surface (Section 13), so its presence doesn't
leak tenant information anywhere a client could observe it. **A client
can never choose, see, or influence a `storage_key`** — no API request
schema anywhere in this story accepts one as input.

## 8. Original Filename

`original_filename` is DISPLAY metadata only —
`app.services.document_validation.sanitize_original_filename`:

- Strips directory components from EITHER path style (`/` and `\`,
  regardless of the server's own OS — a client can send either).
- Strips ASCII control characters (`0x00`-`0x1F`, `0x7F`).
- Collapses surrounding whitespace.
- Truncates to 255 characters.
- Falls back to `"unnamed-file"` if nothing safe remains (empty,
  all-control-characters, a bare path separator, or resolves to `.`/`..`).

Never used to build a filesystem path (Section 7) and never trusted as
an authoritative media type (Section 9) — it is rendered back to a
client purely as a label, sanitized again for `Content-Disposition` at
download time (Section 13).

## 9. Allowed File Types & Signature Validation

A deliberately small allowlist: **PDF, JPEG, PNG**. Detected by
`app.services.document_validation.sniff_media_type`, checking the
uploaded content's first bytes against the well-known magic-byte
signature for each format — `%PDF-`, `\xff\xd8\xff` (JPEG), and the
8-byte PNG signature. Neither the client-declared `Content-Type` nor
the filename's extension is trusted for anything security-relevant — a
file named `photo.png` whose actual bytes are a JPEG is classified as
JPEG, from its content, not its name.

**Deliberately hand-rolled, not a third-party signature-detection
library** (e.g. `python-magic`, which wraps the OS-level `libmagic` —
notoriously fragile to install reliably on Windows and in CI, exactly
what this story was told to avoid). The allowlist is exactly three,
extremely well-known, trivially-distinguished formats — a few
`bytes.startswith()` checks are simpler, add zero third-party
dependencies (no supply-chain surface, nothing to keep updated for CVEs
in a parsing library), and are exactly as reliable as a general-purpose
library for a set this narrow. See
[adr/ADR-0008-document-storage-and-security.md](adr/ADR-0008-document-storage-and-security.md).

Explicitly rejected (no signature match, in this codebase's allowlist):
HTML, SVG, JavaScript, executables (e.g. a Windows PE binary renamed
`.pdf`), Office documents/macros, ZIP/archive formats, and any other
binary type. An empty upload is rejected the same way — an empty
header matches no signature.

## 10. File Size & Streaming

`DOCUMENT_MAX_UPLOAD_BYTES` (default 10 MB, `app.core.config.Settings`)
bounds every upload. **The limit is enforced WHILE READING, never
after** — `app.services.document._validated_chunks` reads the upload in
64 KiB chunks, tracking a running total, and raises
`DocumentTooLargeError` the MOMENT the total exceeds the configured
maximum. An oversized upload is never fully buffered into memory or
fully written to storage first.

An oversized upload that was otherwise a valid, signature-recognized
file still results in a `pending` -> `failed` row (Section 4) — the
upload was ACCEPTED as valid input before the size violation was
detected mid-stream, so this is a lifecycle failure, not an input
rejection.

## 11. SHA-256 Hashing

Computed incrementally, over the SAME chunks streamed to storage, using
Python's standard `hashlib.sha256` — by the time the storage write
completes, the digest is already fully known. Persisted as integrity
metadata (`PatientDocument.sha256`) once the upload succeeds.

**This is integrity metadata, not an authentication or authorization
mechanism.** SHA-256 proves "these bytes are exactly what was written
at upload time" (useful for detecting storage corruption, or verifying
a downloaded file matches what was recorded) — it proves nothing about
who may access a document. Access control is entirely the job of the
API/RBAC layer (Section 15), never the hash.

## 12. Real PostgreSQL Schema (Verified)

```
Check constraints:
    ck_patient_documents_document_type            (document_type allowlist)
    ck_patient_documents_document_status           (status allowlist)
    ck_patient_documents_document_media_type        (media_type allowlist)
    ck_patient_documents_available_has_size_and_hash
        (status <> 'available' OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL))
Foreign-key constraints:
    fk_patient_documents_org_patient_patients        (composite: org+patient ownership)
    fk_patient_documents_org_uploader_memberships     (composite: uploader membership)
    fk_patient_documents_organization_id_organizations
    fk_patient_documents_patient_id_patients
Unique constraints:
    uq_patient_documents_storage_key
```

No `BLOB`/`bytea`/large-object column exists anywhere on
`patient_documents` — verified directly against the live schema (see
the STORY-008 validation report). File bytes never enter PostgreSQL.

## 13. Repository & Service Layers

`app/repositories/patient_document.py` — tenant-scoped
(`get_by_id`, `list_by_patient`, `create`); every read requires an
explicit `organization_id`; never commits; performs no RBAC.

`app.services.document_validation` — pure functions (signature
sniffing, filename sanitization); no I/O, no database access.

`PatientDocumentService` (`app/services/document.py`) — owns the upload
state machine (Section 4), all subsequent lifecycle transitions
(deletion, Section 14), and safe retrieval:

- `upload_document(...)` — Section 4.
- `get_document(organization_id, document_id, patient_id=None)` —
  tenant-scoped, optionally patient-scoped (self-access — Section 16).
  Returns a document of ANY status (auditable visibility for whoever is
  already authorized to see its metadata).
- `list_documents_for_patient(organization_id, patient_id, ...)` —
  tenant- and patient-scoped listing.
- `download_document(...)` — returns `(document, byte_stream)` for a
  currently `available` document only (`DocumentNotAvailableError`, 409,
  otherwise). `storage_key` never leaves this method.
- `delete_document(...)` — Section 14.

## 14. Deletion / Retirement Semantics

**No hard deletion.** `delete_document`: `pending`/`available`/`failed`
-> `deleted`. An already-`deleted` document is REJECTED (not silently
no-op'd) with `InvalidDocumentTransitionError` — the same uniform "one
uncooperative rule for every invalid transition" pattern
`AppointmentService` established in STORY-007.

**Storage deletion is attempted BEFORE the status changes.** If it
raises, the document's status is left **completely unchanged** and
`DocumentDeletionFailedError` (500) propagates — this method never
reports success while the underlying object might still exist.
`DocumentStorage.delete` is idempotent, so this is safe to call
regardless of whether the object was ever actually written (e.g. a
`failed` document whose storage write never completed). Proven directly
in `tests/services/test_document.py::test_delete_document_storage_failure_leaves_status_unchanged`
(a storage double whose `delete()` always raises; the document's status
is re-queried afterward and confirmed unchanged).

Metadata is retained after deletion — sufficient for auditability
(who uploaded what, when, what type, when it was retired) without
retaining the file's actual content once removed from storage.

## 15. RBAC (Authorization Matrix)

| Action | ADMIN | STAFF | PATIENT |
|---|---|---|---|
| Upload | allowed (any patient) | allowed (any patient) | allowed (self only) |
| List | allowed (any patient) | allowed (any patient) | allowed (self only) |
| Get metadata | allowed (any patient) | allowed (any patient) | allowed (self only) |
| Download | allowed (any patient) | allowed (any patient) | allowed (self only) |
| Delete/retire | allowed | allowed | **never** |

**Patient deletion policy (deliberate, conservative choice)**: a
`PATIENT` caller cannot delete or retire a document under any
circumstances in this story — `DELETE .../documents/{id}` requires
`ADMIN`/`STAFF` (`require_roles`), rejecting `PATIENT` with 403 before
any document is even looked up. Rationale: an uploaded administrative
document (identity, insurance, consent, referral) may need to remain
available for the organization's own administrative/compliance
purposes even after a patient would prefer it gone — retirement is an
organizational decision, not a unilateral patient one, in this story's
scope. A future story could introduce a patient-initiated
request-to-remove workflow if a concrete need justifies it; this is not
that.

Every route lives under
`/api/v1/organizations/{organization_id}/patients/{patient_id}/documents`
— `patient_id` is ALWAYS a URL path parameter, not a request-body field,
which makes the self-access boundary structural rather than merely
conventions: see Section 16.

## 16. Patient Self-Access: Never Trusted From The Client

`app.api.v1.endpoints.documents._resolve_patient_id` is the single place
every route resolves `patient_id` through, BEFORE any document
operation runs:

- **`ADMIN`/`STAFF`**: the path `patient_id` must exist in this
  organization (404 otherwise, via `PatientService.get_patient`).
- **`PATIENT`**: the path `patient_id` must equal the caller's OWN
  linked `Patient` record, derived server-side from the authenticated
  `User` (`PatientService.get_own_patient_record`, keyed off
  `User.id` — never trusted from the URL or any request body). Any
  OTHER `patient_id` in the URL 404s — identically to a truly
  nonexistent patient, non-disclosing (see
  [RBAC.md](RBAC.md) Section 10 for the established
  `.../patients/me`-style self-access pattern this generalizes).

Because `patient_id` is a URL path segment (not a request-body field
`PATIENT` could theoretically omit or override), a patient structurally
cannot reach another patient's documents by constructing a request —
there is no code path where a different `patient_id` is ever accepted
for a `PATIENT`-role caller, not merely "ignored" the way
[APPOINTMENTS.md](APPOINTMENTS.md)'s body-based `patient_id` had to be
for booking.

## 17. Download Safety

`GET .../documents/{document_id}/download`:

- Verifies membership, tenant, and patient ownership/self-access (via
  `_resolve_patient_id`, Section 16) before touching the document.
- Verifies the document's status is `available` (409 otherwise).
- Retrieves bytes exclusively through the `DocumentStorage` abstraction
  — the route handler never touches a filesystem path directly.
- **Never exposes `storage_key` or any filesystem/object-storage path**
  in the response — confirmed in `DocumentResponse`'s field set
  (Section 18) and directly asserted in
  `tests/api/test_document_endpoints.py`.
- Sets `Content-Type` to the document's own `media_type` (never
  client-supplied).
- Sets `Content-Disposition: attachment` (never `inline`) — untrusted
  content is never rendered/executed by a browser; a download is always
  offered as a file to save, not displayed. The filename parameter is
  re-sanitized (Section 8) and has embedded double-quotes replaced so it
  can never break out of the quoted `filename="..."` parameter.
- Sets `X-Content-Type-Options: nosniff` — a browser is told never to
  second-guess the declared `Content-Type` by sniffing the content
  itself, closing off a class of content-type-confusion attacks.

## 18. Response Shape & Privacy

`DocumentResponse` exposes exactly: `id`, `organization_id`,
`patient_id`, `uploaded_by_user_id`, `document_type`, `status`,
`original_filename`, `media_type`, `size_bytes`, `sha256`, `created_at`,
`updated_at` — **`storage_key` is never included**, in any response,
anywhere in this API.

## 19. Cross-Tenant & Cross-Patient Protection

Every tenant-owned lookup requires `organization_id` — no unscoped
`get_by_id` exists anywhere in `app.repositories.patient_document`.
Verified end-to-end in `tests/api/test_document_endpoints.py`:

- Org A cannot upload to Org B's patient (`patient_id` in the URL
  doesn't resolve within Org A — 404).
- Org A cannot list Org B's documents.
- Org A cannot retrieve or download Org B's document by UUID.
- Patient A cannot access Patient B's documents (list, get, or
  download) — even within the SAME organization.
- A patient cannot spoof `patient_id` during upload — Section 16.
- Knowledge of a `storage_key` cannot bypass API authorization: no
  endpoint anywhere accepts a `storage_key`, and the download route
  always re-derives it server-side from an already-authorized
  `document_id` lookup — a client literally has no way to submit one.
- Cross-tenant resource lookups are non-disclosing: a nonexistent
  document, a document belonging to a different organization, and a
  document belonging to a different patient within the same
  organization all produce the identical 404 response.

## 20. Malware-Scanning Boundary

**Signature validation is NOT malware scanning.** A file that passes
`sniff_media_type` (Section 9) is confirmed to structurally begin with
the expected magic bytes for PDF/JPEG/PNG — it is NOT confirmed to be
free of embedded exploits, malicious macros disguised within a
well-formed container, or any other malicious payload a well-formed
file of an allowed type could still carry. This story makes no claim
otherwise, and does not build a fake scanner that always reports
"clean."

**Malware scanning is explicitly modeled as a DEPLOYMENT INTEGRATION
BOUNDARY, deferred, not implemented.** Before any real (non-synthetic)
healthcare document upload reaches production, a real antivirus/malware
scanning step (e.g. ClamAV, a cloud provider's file-scanning service)
MUST be integrated into the upload path — most naturally as an
additional stage in `PatientDocumentService.upload_document`'s state
machine (Section 4), between signature validation and marking a
document `available`, so an infected file is caught and marked `failed`
before ever being reported as retrievable. See
[adr/ADR-0008-document-storage-and-security.md](adr/ADR-0008-document-storage-and-security.md)
for why this is deferred rather than stubbed, and what a future
integration must preserve.

## 21. Configuration

`app/core/config.py`:

```
DOCUMENT_STORAGE_BACKEND=local        # only "local" is implemented
DOCUMENT_STORAGE_PATH=local_storage/documents
DOCUMENT_MAX_UPLOAD_BYTES=10485760    # 10 MB
```

No credentials of any kind are configured for document storage in this
story — `LocalDocumentStorage` needs none, and no S3-compatible backend
exists yet to need any (Section 6). `Settings` refuses to start the
application with `DOCUMENT_STORAGE_BACKEND=local` when `APP_ENV` is
`staging`/`production` (`_forbid_local_document_storage_outside_development`)
— the same fail-loud-not-silent pattern already established for
`JWT_SECRET_KEY` outside development (see [RBAC.md](RBAC.md) Section 3).

## 22. Known Limitations

- Malware scanning is not implemented — see Section 20.
- Only PDF, JPEG, and PNG are accepted — no other document format, even
  a legitimate one, can be uploaded in this story.
- `DOCUMENT_MAX_UPLOAD_BYTES` and the allowed-type list are global
  configuration, not per-organization/per-document-type configurable.
- No production object-storage backend exists yet — only
  `LocalDocumentStorage` (local development only).
- No versioning: re-uploading "the same" document type for a patient
  creates a new, independent `PatientDocument` row; there is no concept
  of superseding or replacing a prior upload of the same logical
  document.

## 23. Current vs. Planned

**Current (this story):** `PatientDocument`, `DocumentType`,
`DocumentStatus`, `DocumentMediaType`; database-enforced tenant/patient/
uploader-membership ownership integrity; the `DocumentStorage`
abstraction and `LocalDocumentStorage`; signature-based upload
validation (PDF/JPEG/PNG allowlist); streaming size enforcement and
SHA-256 hashing; the upload state machine with failure recovery; the
full upload/list/get/download/delete API and RBAC matrix above; patient
self-access with a structurally-enforced identity boundary; safe
download headers; soft-deletion/retirement semantics.

**Explicitly not implemented in this story** (later stories): malware
scanning, an S3-compatible (or other) production storage backend, OCR,
document summarization, medical extraction, diagnosis from documents,
vector embeddings, RAG, notifications, `WorkflowRun`, any LLM/agent/
LangGraph integration, a frontend, cloud credentials of any kind.
