"""One-off local dev script: seed a demo organization + admin user.

Not part of the application itself. Run from `backend/` with:
    python scripts/seed_demo_user.py

Loads DATABASE_URL the same way `app.core.config.Settings` normally does,
falling back to `.env.local-backup-do-not-commit` when no `.env` is
present (that's the only DB config found on this machine).
"""

from __future__ import annotations

import asyncio
import secrets
import string
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.security import hash_password
from app.core.config import Settings
from app.db.session import build_engine
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User

DEMO_EMAIL = "demo.admin@agentcare.local"
DEMO_ORG_SLUG = "agentcare-demo"
DEMO_ORG_NAME = "AgentCare Demo Clinic"


def _load_settings() -> Settings:
    backend_dir = Path(__file__).resolve().parent.parent
    if (backend_dir / ".env").exists():
        return Settings()
    backup = backend_dir / ".env.local-backup-do-not-commit"
    if backup.exists():
        return Settings(_env_file=backup)
    return Settings()


def _generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(14))
    return f"Demo-{body}!"


async def main() -> None:
    settings = _load_settings()
    if settings.database_url is None:
        raise SystemExit(
            "DATABASE_URL is not configured (checked .env and "
            ".env.local-backup-do-not-commit). Set it before seeding."
        )

    engine = build_engine(settings.database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        existing_user = (
            await session.execute(select(User).where(User.email == DEMO_EMAIL))
        ).scalar_one_or_none()

        if existing_user is not None:
            membership = (
                await session.execute(
                    select(OrganizationMembership).where(
                        OrganizationMembership.user_id == existing_user.id
                    )
                )
            ).scalars().first()
            print("Demo user already exists (password unchanged, hash cannot be reversed):")
            print(f"Email: {DEMO_EMAIL}")
            if membership is not None:
                print(f"Organization ID: {membership.organization_id}")
                print(f"Role: {membership.role}")
            await engine.dispose()
            return

        org = (
            await session.execute(
                select(Organization).where(Organization.slug == DEMO_ORG_SLUG)
            )
        ).scalar_one_or_none()
        if org is None:
            org = Organization(
                name=DEMO_ORG_NAME,
                slug=DEMO_ORG_SLUG,
                organization_type=OrganizationType.CLINIC,
            )
            session.add(org)
            await session.flush()

        password = _generate_password()
        user = User(email=DEMO_EMAIL, password_hash=hash_password(password))
        session.add(user)
        await session.flush()

        membership = OrganizationMembership(
            organization_id=org.id,
            user_id=user.id,
            role=Role.ADMIN,
        )
        session.add(membership)
        await session.commit()

        print("Demo credentials created:")
        print(f"Email: {DEMO_EMAIL}")
        print(f"Password: {password}")
        print(f"Organization ID: {org.id}")
        print(f"Role: {membership.role}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
