"""Report locally knowable deployment checks without claiming an infrastructure audit."""

import argparse
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from .authentication import load_auth_provider
from .deployment import DeploymentConfig


class SecurityCheck(BaseModel):
    id: str
    status: Literal["pass", "warning", "unverified"]
    title: str
    detail: str


class SecurityReport(BaseModel):
    summary: Literal["local", "review"]
    checks: list[SecurityCheck]
    counts: dict[str, int]


def security_report(config: DeploymentConfig) -> SecurityReport:
    """Describe protections and remaining checks without exposing identities or options."""
    if config.mode == "local":
        return SecurityReport(summary="local", checks=[], counts={"warnings": 0, "unverified": 0})
    checks = [
        SecurityCheck(
            id="authentication",
            status="pass",
            title="Authentication configured",
            detail="The server requires a verified identity and an explicit membership match. "
            "An installed provider is trusted server code.",
        ),
        SecurityCheck(
            id="access_boundary",
            status="pass",
            title="Workspace access restricted",
            detail="Host and origin checks are enabled. Agent identities cannot use browser "
            "or server administration routes. All human members share this workspace.",
        ),
        SecurityCheck(
            id="preview_boundary",
            status="pass",
            title="Preview resources isolated",
            detail="Separate HTTPS origins are configured. Preview links are restricted to "
            "one revision and expire after one hour or a restart.",
        ),
        SecurityCheck(
            id="mfa",
            status="unverified",
            title="Require MFA for every human member",
            detail="Check the identity provider's sign-in policy, enroll recovery methods and "
            "test an unlisted account. Studio cannot inspect the provider's MFA policy.",
        ),
        SecurityCheck(
            id="origin_network",
            status="unverified",
            title="Keep the server port private",
            detail="Use the loopback Compose binding and an HTTPS tunnel. Verify firewall rules "
            "and that the origin port cannot be reached from the public internet.",
        ),
        SecurityCheck(
            id="preview_credentials",
            status="unverified",
            title="Check preview cookie isolation",
            detail="Do not scope login cookies to a parent domain shared with previews. Verify "
            "host-only cookies or use an unrelated domain. "
            "Preview requests must carry no login credentials.",
        ),
        SecurityCheck(
            id="logs",
            status="unverified",
            title="Keep preview links out of logs",
            detail="The hosted Compose override disables access logs. Check tunnel and proxy "
            "logs too: preview URLs contain temporary access tokens.",
        ),
        SecurityCheck(
            id="recovery",
            status="unverified",
            title="Test revocation and workspace recovery",
            detail="Revoke a test agent credential and verify access stops. Protect backups and "
            "test a restore into a separate volume before depending on this server.",
        ),
    ]
    workspace = (urlsplit(config.public_url).hostname or "").split(".")
    preview = (urlsplit(config.preview_url).hostname or "").split(".")
    # This is a conservative shared-suffix warning, not a public-suffix or cookie-scope proof.
    if workspace[-2:] == preview[-2:]:
        checks[5].status = "warning"
        checks[5].detail = (
            "These hosts share a domain suffix. A parent-domain login cookie could reach "
            "the preview service. Verify host-only cookies or choose an unrelated domain."
        )
    if not config.member_emails:
        checks.append(
            SecurityCheck(
                id="human_members",
                status="warning",
                title="No browser members configured",
                detail="Only agent identities can connect. "
                "Add exact member email addresses to enable the browser.",
            )
        )
    if config.auth_provider != "cloudflare":
        checks.append(
            SecurityCheck(
                id="custom_provider",
                status="unverified",
                title="Review the installed authentication module",
                detail="Pin and review the provider package. Test signature and claim validation, "
                "invalid credentials, outages and revocation. "
                "Providers run with server privileges.",
            )
        )
    options = config.auth_options or {}
    if any(
        isinstance(value, str) and value.startswith("replace-with-") for value in options.values()
    ):
        checks.append(
            SecurityCheck(
                id="example_options",
                status="warning",
                title="Example authentication settings remain",
                detail="Replace the example provider settings with values "
                "from your own account before connecting.",
            )
        )
    filename = os.environ.get("ARTIFACT_STUDIO_HOSTED_CONFIG")
    if filename:
        try:
            if Path(filename).stat().st_mode & 0o004:
                checks.append(
                    SecurityCheck(
                        id="config_readable",
                        status="warning",
                        title="Configuration is readable by other OS users",
                        detail="Use an owner-controlled file readable by the server account, "
                        "for example mode 640. "
                        "Keep configuration outside shared collections and repositories.",
                    )
                )
        except OSError:
            checks.append(
                SecurityCheck(
                    id="config_changed",
                    status="warning",
                    title="Configuration file is no longer accessible",
                    detail="The server still uses its startup settings. "
                    "Restore the protected file and restart "
                    "after validating any changes.",
                )
            )
    return SecurityReport(
        summary="review",
        checks=checks,
        counts={
            "warnings": sum(c.status == "warning" for c in checks),
            "unverified": sum(c.status == "unverified" for c in checks),
        },
    )


def main() -> int:
    """Validate configuration locally; never contact provider accounts or print secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit a structured, redacted report.")
    args = parser.parse_args()
    try:
        config = DeploymentConfig.from_env()
    except ValueError as error:
        print(json.dumps({"status": "blocked", "detail": str(error)}))
        return 2
    if config.mode == "hosted":
        try:
            load_auth_provider(config)
        except Exception:
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "detail": "Authentication provider could not be loaded. "
                        "Check its installed package and auth_options.",
                    }
                )
            )
            return 2
    report = security_report(config)
    if args.json:
        print(report.model_dump_json())
    else:
        print(
            "Local mode: keep the listener private; there is no login."
            if config.mode == "local"
            else "Configuration accepted. External deployment checks still need review:"
        )
        for check in report.checks:
            print(f"[{check.status.upper()}] {check.title}: {check.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
