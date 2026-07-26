"""Repositories: tenant-scoped persistence/query operations.

A repository's job is data access only — it never performs RBAC, never
decodes a JWT, never owns HTTP concerns, and never autonomously commits a
business transaction (`add`/`flush`/`query` only — see
docs/DATABASE.md "Transaction Ownership Philosophy" and
docs/ARCHITECTURE.md "Backend Layering Philosophy"). Transaction
completion is owned by the service layer above it.
"""
