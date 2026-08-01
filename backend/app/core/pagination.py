"""Shared pagination bounds (Sprint 2).

`MAX_PAGE_SIZE` is the one upper bound every `limit` query parameter
across the list endpoints (`app.api.v1.endpoints.appointments`,
`approvals`, `departments`, `documents`, `patients`, `practitioners`,
`workflows`) is validated against via `Query(ge=1, le=MAX_PAGE_SIZE)` —
a single shared constant so the bound can't silently drift to a
different value in one endpoint than another. Previously `limit` had no
upper bound at all: a caller could request an effectively unbounded
`SELECT ... LIMIT n`.

Not a `Settings` field — this is a request-validation bound, not
deployment configuration; no known need to vary it per environment. The
existing default (`limit: int = 50`, unchanged) stays well under this
ceiling for every current caller — see e.g.
`frontend/services/workflows.ts#listWorkflows` and
`frontend/app/org/[organizationId]/analytics/page.tsx`'s bounded
recent-runs sample (`limit: 100`).
"""

from __future__ import annotations

MAX_PAGE_SIZE = 200
