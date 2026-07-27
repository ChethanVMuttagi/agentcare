"""Storage-layer exceptions.

Deliberately NOT `app.core.exceptions.AppException` subclasses — this
package has no knowledge of HTTP status codes or the API layer at all
(see `app/storage/base.py`'s module docstring). `app.services.document`
catches these and translates them into the appropriate domain
`AppException` for its callers.
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for every exception this package raises."""


class StorageObjectNotFoundError(StorageError):
    """No object exists under the given storage key."""


class StorageKeyInvalidError(StorageError):
    """The given storage key does not resolve to a location inside the
    configured storage root (e.g. a path-traversal attempt), or is
    otherwise not a key this backend could have generated itself.

    Defense-in-depth: `storage_key` is always server-generated and opaque
    (see `app.services.document`) — a legitimate caller should never be
    able to trigger this. If it fires, treat it as a bug or an attack,
    not routine input validation.
    """
