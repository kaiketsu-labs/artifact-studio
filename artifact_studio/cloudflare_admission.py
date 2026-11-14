"""Narrow Cloudflare Access group membership adapter, disabled unless selected.

The group must be dedicated to this deployment and exclusively managed by Studio.
No application, policy, MFA or group-selector changes are made by this adapter.
"""

import json
import os
import re
import time
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx

from .multiplayer_access import email_address

if TYPE_CHECKING:
    from .invitation_provisioning import AdmissionContext

MANAGED_GROUP_NAME = "Artifact Studio invitation admission"
_IDENTIFIER = re.compile(r"(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})")


class AdmissionUnavailable(RuntimeError):
    """A deliberately generic failure; provider response bodies can contain secrets."""


class CloudflareGroupAdmission:
    """Reconcile email-only membership of a pinned, existing Access group."""

    def __init__(
        self,
        context: AdmissionContext,
        *,
        account: str,
        application: str,
        policy: str,
        group: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not all(_IDENTIFIER.fullmatch(value) for value in (account, application, policy, group)):
            raise ValueError(
                "Cloudflare admission requires pinned account/application/policy/group IDs."
            )
        if not token or any(character.isspace() for character in token):
            raise ValueError("Set the dedicated Cloudflare admission token in the environment.")
        self.context = context
        self.account, self.application, self.policy, self.group = (
            account,
            application,
            policy,
            group,
        )
        self.namespace = f"cloudflare/{account}/{application}/{policy}/{group}"
        self._token = token
        self._transport = transport

    @classmethod
    def from_env(cls, context: AdmissionContext) -> CloudflareGroupAdmission:
        prefix = "ARTIFACT_STUDIO_CLOUDFLARE_ADMISSION_"
        return cls(
            context,
            account=os.environ.get(prefix + "ACCOUNT", ""),
            application=os.environ.get(prefix + "APPLICATION", ""),
            policy=os.environ.get(prefix + "POLICY", ""),
            group=os.environ.get(prefix + "GROUP", ""),
            token=os.environ.get(prefix + "TOKEN", ""),
        )

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        deadline: float,
        *,
        body: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, object], str | None]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AdmissionUnavailable("Cloudflare admission is unavailable.")
        raw = bytearray()
        with client.stream(
            method,
            "https://api.cloudflare.com/client/v4/accounts/" + self.account + path,
            json=body,
            headers=headers,
            timeout=min(remaining, 5),
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 262144 or time.monotonic() > deadline:
                    raise AdmissionUnavailable("Cloudflare admission is unavailable.")
            value: object = json.loads(raw)
            if not isinstance(value, dict):
                raise AdmissionUnavailable("Cloudflare admission is unavailable.")
            envelope = cast(dict[str, object], value)
            result = envelope.get("result")
            if envelope.get("success") is not True or not isinstance(result, dict):
                raise AdmissionUnavailable("Cloudflare admission is unavailable.")
            return cast(dict[str, object], result), response.headers.get("etag")

    def _members(self, group: dict[str, object]) -> set[str]:
        include = group.get("include")
        if (
            group.get("id") != self.group
            or group.get("name") != MANAGED_GROUP_NAME
            or group.get("require") != []
            or group.get("exclude") != []
            or not isinstance(include, list)
        ):
            raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
        emails: set[str] = set()
        for item in cast(list[object], include):
            if not isinstance(item, dict):
                raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
            item = cast(dict[str, object], item)
            if set(item) != {"email"}:
                raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
            selector = item["email"]
            if not isinstance(selector, dict):
                raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
            selector = cast(dict[str, object], selector)
            if set(selector) != {"email"}:
                raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
            address = selector["email"]
            if not isinstance(address, str):
                raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
            emails.add(email_address(address))
        return emails

    def reconcile(self, email: str, eligible: bool, operation_id: str, *, timeout: float) -> None:
        """Verify the pinned MFA policy, then change only email group membership.

        Cloudflare Access groups have no per-member mutation API. Require exclusive
        management of the dedicated group. Use a conditional update if Cloudflare
        returns an ETag, and verify the complete resulting membership afterward.
        """
        deadline = time.monotonic() + min(timeout, 10)
        try:
            with httpx.Client(
                headers={"Authorization": "Bearer " + self._token},
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                organization, _ = self._request(client, "GET", "/access/organizations", deadline)
                application, _ = self._request(
                    client,
                    "GET",
                    f"/access/apps/{self.application}",
                    deadline,
                )
                policy, _ = self._request(
                    client,
                    "GET",
                    f"/access/apps/{self.application}/policies/{self.policy}",
                    deadline,
                )
                if (
                    organization.get("auth_domain") != urlsplit(self.context.issuer).hostname
                    or application.get("id") != self.application
                    or application.get("aud") != self.context.audience
                    or application.get("domain") != urlsplit(self.context.public_url).netloc
                    or application.get("type") != "self_hosted"
                    or policy.get("id") != self.policy
                    or policy.get("decision") != "allow"
                    or policy.get("include") != [{"group": {"id": self.group}}]
                    or not isinstance(policy.get("require"), list)
                    or {"auth_method": {"auth_method": "mfa"}}
                    not in cast(list[object], policy["require"])
                ):
                    raise AdmissionUnavailable("Cloudflare admission configuration does not match.")
                path = f"/access/groups/{self.group}"
                group, etag = self._request(client, "GET", path, deadline)
                members = self._members(group)
                expected = members | {email} if eligible else members - {email}
                if members == expected:
                    return
                body: dict[str, object] = {
                    "name": MANAGED_GROUP_NAME,
                    "include": [{"email": {"email": member}} for member in sorted(expected)],
                    "require": [],
                    "exclude": [],
                }
                headers = {"X-Idempotency-Key": operation_id}
                if etag:
                    headers["If-Match"] = etag
                self._request(client, "PUT", path, deadline, body=body, headers=headers)
                confirmed, _ = self._request(client, "GET", path, deadline)
                if self._members(confirmed) != expected:
                    raise AdmissionUnavailable("Cloudflare admission result is uncertain.")
        except httpx.HTTPError, ValueError, TypeError, KeyError:
            raise AdmissionUnavailable("Cloudflare admission is unavailable.") from None
