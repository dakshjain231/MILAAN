"""Runtime safety settings for MILAAN deployments.

Values come only from the process environment (the local launchers also
merge the repository-root ``.env``). No secrets are written to logs or the
database.

Authentication policy (enforced by ``milaan_web.create_app``):

* ``APP_ENV=production`` requires authentication unless
  ``MILAAN_AUTH_REQUIRED=false`` is set explicitly.
* When authentication is required, ``MILAAN_BASIC_AUTH_USER`` and
  ``MILAAN_BASIC_AUTH_PASSWORD_HASH`` (a Werkzeug password hash) must be set,
  otherwise the app refuses to start. HTTP Basic credentials are only safe
  over HTTPS, so production must sit behind a TLS-terminating proxy.
* ``MILAAN_ALLOWED_EMAIL_DOMAINS`` is a policy for an identity-aware proxy in
  front of MILAAN; the app itself does not perform OIDC sign-in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_PRODUCTION_ENVS = {"production", "prod"}


def _bool(name: str, value: str | None, *, default: bool) -> bool:
    text = (value or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class SecurityConfig:
    environment: str
    auth_required: bool
    allowed_email_domains: tuple[str, ...]
    basic_auth_user: str | None = None
    basic_auth_password_hash: str | None = None

    @property
    def production(self) -> bool:
        return self.environment in _PRODUCTION_ENVS

    @property
    def basic_auth_configured(self) -> bool:
        return bool(self.basic_auth_user and self.basic_auth_password_hash)


def load_security_config(
    environ: Mapping[str, str] | None = None,
) -> SecurityConfig:
    """Load deployment policy; production requires explicit OIDC sign-in."""
    env = os.environ if environ is None else environ
    environment = (env.get("APP_ENV") or "development").strip().lower()
    if not environment:
        raise ValueError("APP_ENV must not be empty")
    default_auth = environment in _PRODUCTION_ENVS
    auth_required = _bool(
        "MILAAN_AUTH_REQUIRED", env.get("MILAAN_AUTH_REQUIRED"),
        default=default_auth,
    ) if env.get("MILAAN_AUTH_REQUIRED") is not None else default_auth
    domains = tuple(
        domain.strip().lower().lstrip("@")
        for domain in (env.get("MILAAN_ALLOWED_EMAIL_DOMAINS") or "").split(",")
        if domain.strip()
    )
    return SecurityConfig(
        environment,
        auth_required,
        domains,
        basic_auth_user=(env.get("MILAAN_BASIC_AUTH_USER") or "").strip() or None,
        basic_auth_password_hash=(env.get("MILAAN_BASIC_AUTH_PASSWORD_HASH") or "").strip() or None,
    )


def email_is_allowed(email: str | None, domains: tuple[str, ...]) -> bool:
    """Allow every verified identity when no domain policy is configured."""
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        return False
    return not domains or normalized.rsplit("@", 1)[1] in domains
