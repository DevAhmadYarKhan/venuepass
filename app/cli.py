"""Trusted local account management; no public permission-granting endpoint."""

import argparse
import asyncio
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.errors import UserNotFound
from app.services.users import promote_organizer, promote_venue_manager


async def promote(email: str, command: str = "promote-organizer") -> None:
    """Own a short-lived session and release database resources on every exit."""
    engine = create_async_engine(str(get_settings().database_url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            # Only the explicitly registered CLI commands can select a permission.
            operation = promote_venue_manager if command == "promote-venue-manager" else promote_organizer
            await operation(session, email)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Parse and normalize an account email, returning a shell-friendly exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("promote-organizer", help="Grant event creation permission")
    command.add_argument("email")
    venue_command = commands.add_parser("promote-venue-manager", help="Grant venue creation permission")
    venue_command.add_argument("email")
    args = parser.parse_args(argv)
    try:
        # Match registration normalization and validation before looking up the account.
        email = str(TypeAdapter(EmailStr).validate_python(args.email.strip().lower()))
    except ValidationError:
        print("Invalid email address", file=sys.stderr)
        return 1
    try:
        asyncio.run(promote(email, args.command))
    except UserNotFound:
        print(f"No account found for {email}", file=sys.stderr)
        return 1
    label = "Venue manager" if args.command == "promote-venue-manager" else "Organizer"
    print(f"{label} permission enabled for {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
