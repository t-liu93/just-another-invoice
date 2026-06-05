"""Email sending service using aiosmtplib + Jinja2 templates.

SMTP configuration is read dynamically from the settings table on each call
(so that changes in the settings UI take effect immediately).  When no SMTP
settings exist in the DB, individual ``SMTP_*`` environment variables serve
as fallback (``jai.config.Settings``).

Templates
---------
Jinja2 HTML templates live in ``jai/templates/email/``.  Autoescape is
**always enabled** (red-line 7 – XSS prevention for user-supplied content).
"""

from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from jai.config import get_settings
from jai.models._enums import SettingLevel
from jai.schemas.setting import SETTING_KEY_SMTP, SmtpSettings

logger = logging.getLogger("jai.email")

# ---------------------------------------------------------------------------
# Jinja2 environment (autoescape ON → red-line 7)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "htm"]),
)


# ---------------------------------------------------------------------------
# SMTP configuration resolution
# ---------------------------------------------------------------------------


async def _get_smtp_config(session: AsyncSession) -> SmtpSettings | None:
    """Load SMTP settings from the DB, falling back to env vars.

    Returns ``None`` if neither source provides a usable configuration
    (specifically, if ``host`` is empty) or if the configuration is
    structurally invalid (e.g. ``from_email`` is not a valid email).
    Invalid configurations are logged and safely degraded to ``None``.
    """
    from jai.services.settings import get_setting

    try:
        cfg = await get_setting(
            session,
            SETTING_KEY_SMTP,
            level=SettingLevel.GLOBAL,
            value_type=SmtpSettings,
        )
    except Exception:
        logger.warning("Failed to read SMTP settings from DB", exc_info=True)
        cfg = None

    if cfg is not None and cfg.host:
        return cfg

    # Env fallback.
    settings = get_settings()
    if settings.smtp_host:
        try:
            return SmtpSettings(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                from_email=settings.smtp_from_email,
                from_name=settings.smtp_from_name,
                use_tls=settings.smtp_use_tls,
                use_ssl=settings.smtp_use_ssl,
            )
        except Exception:
            logger.warning(
                "SMTP env variables are incomplete or invalid – "
                "skipping env fallback",
                exc_info=True,
            )

    return None


def _configured(cfg: SmtpSettings | None) -> bool:
    """Return ``True`` if the configuration has enough data to send."""
    return cfg is not None and bool(cfg.host) and bool(cfg.from_email)


# ---------------------------------------------------------------------------
# Low-level send
# ---------------------------------------------------------------------------


async def _send_mail(
    cfg: SmtpSettings,
    to: str,
    subject: str,
    html_body: str,
) -> None:
    """Send a single HTML email using the provided SMTP configuration."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = (
        f"{cfg.from_name} <{cfg.from_email}>" if cfg.from_name else cfg.from_email
    )
    msg["To"] = to

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if cfg.use_ssl:
        # Implicit TLS (port 465).
        await aiosmtplib.send(
            msg,
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
            use_tls=True,
        )
    else:
        # STARTTLS (port 587) or plain.
        await aiosmtplib.send(
            msg,
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
            start_tls=cfg.use_tls,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_password_reset_email(
    session: AsyncSession,
    to: str,
    reset_url: str,
    locale: str = "en",
) -> None:
    """Send a password-reset email with a link containing the token.

    Parameters
    ----------
    session:
        Database session (used to read SMTP settings).
    to:
        Recipient email address.
    reset_url:
        The full URL the user should click (includes the token).
    locale:
        Language code for the email template (``"en"`` or ``"zh"``).
    """
    cfg = await _get_smtp_config(session)
    if not _configured(cfg):
        logger.warning("SMTP not configured – password-reset email not sent to %s", to)
        return
    assert cfg is not None  # guaranteed by _configured() check above

    template_name = f"reset-password-{locale}.html"
    # Fallback to English if the locale template is missing.
    if not (_TEMPLATES_DIR / template_name).is_file():
        template_name = "reset-password-en.html"

    template = _jinja_env.get_template(template_name)
    html = template.render(reset_url=reset_url)

    subject = (
        "重置您的密码" if locale == "zh" else "Reset your password"
    )
    await _send_mail(cfg, to, subject, html)


async def send_test_email(session: AsyncSession, to: str) -> None:
    """Send a test email to verify SMTP connectivity.

    Raises ``RuntimeError`` if SMTP is not configured or sending fails.
    """
    cfg = await _get_smtp_config(session)
    if not _configured(cfg):
        raise RuntimeError("SMTP is not configured.")
    assert cfg is not None  # guaranteed by _configured() check above

    html = "<p>This is a test email from <strong>Just Another Invoice</strong>.</p>"
    subject = "JAI – Test Email"
    await _send_mail(cfg, to, subject, html)


async def is_smtp_configured(session: AsyncSession) -> bool:
    """Return ``True`` if SMTP settings are present (DB or env fallback)."""
    cfg = await _get_smtp_config(session)
    return _configured(cfg)
