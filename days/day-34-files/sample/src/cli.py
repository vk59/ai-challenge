"""CLI entry point for the synthetic API."""
from .auth import login


def sign_in(email: str, password: str) -> dict:
    return login(email, password)
