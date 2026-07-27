"""Document object-storage abstraction.

`app.storage.base.DocumentStorage` is the interface
`app.services.document.PatientDocumentService` depends on — it never talks
to a filesystem, an SDK, or credentials directly. `app.storage.local`
provides the only implementation that exists today
(`LocalDocumentStorage`, filesystem-backed, LOCAL DEVELOPMENT ONLY — see
docs/DOCUMENTS.md). A future story may add an S3-compatible
implementation behind the same interface without changing
`PatientDocumentService` at all.
"""

from __future__ import annotations
