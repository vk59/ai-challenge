"""Web entry point for the synthetic API."""
from .auth import login


def sign_in(payload: dict) -> dict:
    return login(payload["email"], payload["password"], mfa_code=payload.get("mfa_code"))
