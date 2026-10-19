"""Synthetic account API. No network, database or credentials."""


def login(email: str, password: str, *, mfa_code: str | None = None) -> dict:
    """Validate input shape and return synthetic authentication metadata."""
    if not email or not password:
        raise ValueError("Email and password are required")
    return {"authenticated": True, "expires_in": 3600, "mfa_enabled": bool(mfa_code)}
