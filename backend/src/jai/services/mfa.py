"""MFA(TOTP) service – secret generation, URI building, code verification.

Uses ``pyotp`` for TOTP generation and validation.  The time-step window
tolerance is configurable (default: 1 step before/after the current step)
to accommodate clock drift between the user's device and the server.

Pending secrets
---------------
During the MFA setup flow (``POST /auth/mfa/setup`` → ``POST /auth/mfa/verify``),
a "pending" TOTP secret is held in memory keyed by user ID.  It is consumed
(on success) by the verify endpoint and persists across retries (on wrong code).
The pending store is a simple ``dict`` — acceptable for a single-process
deployment; multi-process deployments (future) would need a shared store.
"""

from __future__ import annotations

import pyotp

# ---------------------------------------------------------------------------
# Pending TOTP secret store (in-process, single-process)
# ---------------------------------------------------------------------------

#: Maps ``str(user.id)`` → base32 TOTP secret awaiting first verification.
_pending_totp_secrets: dict[str, str] = {}


def store_pending_secret(user_id: str, secret: str) -> None:
    """Store a pending TOTP secret for the MFA setup flow."""
    _pending_totp_secrets[user_id] = secret


def get_pending_secret(user_id: str) -> str | None:
    """Return the pending TOTP secret without removing it (allows retries)."""
    return _pending_totp_secrets.get(user_id)


def pop_pending_secret(user_id: str) -> str | None:
    """Remove and return the pending TOTP secret (called on successful verify)."""
    return _pending_totp_secrets.pop(user_id, None)


def _clear_pending_secrets() -> None:
    """Clear all pending secrets (testing only)."""
    _pending_totp_secrets.clear()


# ---------------------------------------------------------------------------
# TOTP helpers
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Generate a cryptographically random base32 TOTP secret."""
    return pyotp.random_base32()


def build_otpauth_uri(
    secret: str,
    email: str,
    issuer: str = "JAI",
) -> str:
    """Build an ``otpauth://totp/`` provisioning URI for authenticator apps."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp_code(
    secret: str,
    code: str,
    valid_window: int = 1,
) -> bool:
    """Verify a TOTP code against the given secret.

    Parameters
    ----------
    secret:
        Base32-encoded TOTP secret.
    code:
        6-digit code from the authenticator app.
    valid_window:
        Number of 30-second time steps (before/after the current step) to
        accept.  Default ``1`` allows the previous, current, and next step.

    Returns
    -------
    ``True`` if the code is valid within the window, ``False`` otherwise.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=valid_window)
