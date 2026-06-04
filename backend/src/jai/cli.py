"""JAI command-line interface.

Provides operational commands for self-hosted administrators who have
direct access to the server.  These commands bypass the application's
HTTP layer and interact with the database directly.

Usage::

    jai set-password <email>
    jai reset-mfa <email>
"""

from __future__ import annotations

import asyncio
import sys

import sqlalchemy as sa

from jai.auth.user_manager import hash_password
from jai.db import get_engine, get_session_maker
from jai.models.user import User


async def _set_password(email: str, new_password: str) -> None:
    """Reset a user's password directly in the database."""
    engine = get_engine()
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = sa.select(User).where(User.email == email)  # type: ignore[arg-type]
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            print(f"Error: no user found with email '{email}'.", file=sys.stderr)
            sys.exit(1)

        user.hashed_password = hash_password(new_password)
        session.add(user)
        await session.commit()
        print(f"Password updated for '{email}'.")
    await engine.dispose()


async def _reset_mfa(email: str) -> None:
    """Clear a user's TOTP secret so they are prompted to re-bind MFA."""
    engine = get_engine()
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = sa.select(User).where(User.email == email)  # type: ignore[arg-type]
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            print(f"Error: no user found with email '{email}'.", file=sys.stderr)
            sys.exit(1)

        user.totp_secret = None
        user.mfa_enabled = False
        session.add(user)
        await session.commit()
        print(f"MFA reset for '{email}'. The user will be prompted to set up MFA on next login.")
    await engine.dispose()


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: jai <command> [args]", file=sys.stderr)
        print("Commands:", file=sys.stderr)
        print("  set-password <email>  Set a new password for the user", file=sys.stderr)
        print("  reset-mfa <email>     Reset MFA for the user", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "set-password":
        if len(sys.argv) < 3:
            print("Usage: jai set-password <email>", file=sys.stderr)
            sys.exit(1)
        email = sys.argv[2]
        import getpass

        password = getpass.getpass("New password: ")
        if len(password) < 8:
            print("Error: password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
        asyncio.run(_set_password(email, password))

    elif command == "reset-mfa":
        if len(sys.argv) < 3:
            print("Usage: jai reset-mfa <email>", file=sys.stderr)
            sys.exit(1)
        email = sys.argv[2]
        asyncio.run(_reset_mfa(email))

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Enables ``python -m jai.cli ...`` — used by the runtime container's
    # ``jai`` wrapper, where the project is not pip-installed (it runs via
    # ``PYTHONPATH``).
    main()
