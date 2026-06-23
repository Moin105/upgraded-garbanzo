"""Optional OIDC / SSO bearer auth for the gateway.

When configured, a JWT from an enterprise IdP (Okta, Auth0, Entra ID, Keycloak,
Google Workspace, …) is accepted as a bearer token *alongside* static API keys:
the gateway validates the token's signature against the issuer's JWKS and maps
its claims to a Principal (team + scopes). Static keys stay for service accounts,
CI, and fully air-gapped setups that can't reach an IdP.

Enable by setting ``KARST_OIDC_ISSUER``; everything else has sane defaults:

    KARST_OIDC_ISSUER        https://login.example.com/   (presence enables OIDC)
    KARST_OIDC_AUDIENCE      expected `aud` (recommended; verified when set)
    KARST_OIDC_JWKS_URL      explicit JWKS url (else resolved via discovery)
    KARST_OIDC_TEAM_CLAIM    claim holding the team / org id   (default "team")
    KARST_OIDC_SCOPES_CLAIM  claim holding scopes / roles      (default "scope")

Uses PyJWT (already a transitive dependency of the MCP stack).
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

from .keys import Principal


@dataclass
class OidcConfig:
    issuer: str
    audience: str | None = None
    jwks_url: str | None = None
    team_claim: str = "team"
    scopes_claim: str = "scope"
    repos_claim: str = "repos"
    algorithms: tuple[str, ...] = ("RS256", "RS512", "ES256")


def _scopes_from(value) -> tuple[str, ...]:
    """Normalise an OAuth `scope` string or a roles/permissions array to a tuple.

    Fails CLOSED: a missing/empty/unrecognised scope claim yields NO scopes
    (deny), not the default toolset — an SSO token must carry explicit scopes."""
    if isinstance(value, str):
        return tuple(s for s in value.replace(",", " ").split() if s)
    if isinstance(value, (list, tuple)):
        return tuple(str(s) for s in value if s)
    return ()


def _repos_from(value) -> tuple[str, ...]:
    """Allowed repos from a claim. Absent -> "*" (all repos for the team); the
    team_id is the tenant boundary, repos is the optional finer control."""
    if isinstance(value, str):
        return tuple(s for s in value.replace(",", " ").split() if s) or ("*",)
    if isinstance(value, (list, tuple)):
        return tuple(str(s) for s in value if s) or ("*",)
    return ("*",)


class OidcVerifier:
    def __init__(self, config: OidcConfig) -> None:
        self.config = config
        self._jwk_client = None  # lazy — resolved on first verify

    @classmethod
    def from_env(cls) -> "OidcVerifier | None":
        issuer = os.environ.get("KARST_OIDC_ISSUER")
        if not issuer:
            return None
        audience = os.environ.get("KARST_OIDC_AUDIENCE") or None
        if audience is None:
            # Fail closed: without an audience, any valid-signature token from the
            # issuer (incl. tokens minted for a different relying party) would be
            # accepted. Require it.
            raise ValueError(
                "KARST_OIDC_AUDIENCE is required when KARST_OIDC_ISSUER is set — "
                "set it to this gateway's registered client/audience id so tokens "
                "for other apps in your IdP can't authenticate here."
            )
        return cls(
            OidcConfig(
                issuer=issuer,
                audience=audience,
                jwks_url=os.environ.get("KARST_OIDC_JWKS_URL") or None,
                team_claim=os.environ.get("KARST_OIDC_TEAM_CLAIM", "team"),
                scopes_claim=os.environ.get("KARST_OIDC_SCOPES_CLAIM", "scope"),
                repos_claim=os.environ.get("KARST_OIDC_REPOS_CLAIM", "repos"),
            )
        )

    def _jwks_url(self) -> str:
        if self.config.jwks_url:
            return self.config.jwks_url
        disc = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        with urllib.request.urlopen(disc, timeout=10) as r:  # noqa: S310 (operator-configured issuer)
            doc = json.loads(r.read().decode("utf-8"))
        return doc["jwks_uri"]

    def _client(self):
        if self._jwk_client is None:
            import jwt

            self._jwk_client = jwt.PyJWKClient(self._jwks_url())
        return self._jwk_client

    def verify(self, token: str | None) -> Principal | None:
        # Only attempt OIDC on something shaped like a JWT (header.payload.sig);
        # API keys (kst_sk_…) fall straight through to key auth without a JWKS
        # fetch. Signature, issuer, audience and expiry are all enforced below.
        if not token or token.count(".") != 2:
            return None
        import jwt

        try:
            signing_key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.config.algorithms),
                audience=self.config.audience,
                issuer=self.config.issuer,
                options={"verify_aud": True, "require": ["exp", "iss"]},
            )
        except Exception:
            return None
        return self.principal_from_claims(claims)

    def principal_from_claims(self, claims: dict) -> Principal | None:
        """Map validated JWT claims to a Principal. Pure (no network/crypto), so
        it's unit-testable on its own."""
        # Use ONLY the operator-configured team claim — no silent fallback to
        # org/tid, which could land a token in the wrong tenant bucket.
        team = claims.get(self.config.team_claim)
        if not team:
            return None
        scopes = _scopes_from(claims.get(self.config.scopes_claim))
        repos = _repos_from(claims.get(self.config.repos_claim))
        label = str(
            claims.get("email")
            or claims.get("preferred_username")
            or claims.get("sub")
            or "oidc"
        )
        # key_id 0 marks a non-key (SSO) principal in the usage/audit log.
        return Principal(key_id=0, team_id=str(team), label=label, scopes=scopes, repos=repos)
