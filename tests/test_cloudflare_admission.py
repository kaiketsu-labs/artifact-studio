"""Mock Cloudflare HTTP contract: no external provider calls or account mutations."""

import json
from dataclasses import replace

import httpx
import pytest

from artifact_studio.cloudflare_admission import (
    MANAGED_GROUP_NAME,
    AdmissionUnavailable,
    CloudflareGroupAdmission,
)
from artifact_studio.invitation_provisioning import AdmissionContext

ACCOUNT, APP, POLICY, GROUP = "a" * 32, "b" * 32, "c" * 32, "d" * 32
CONTEXT = AdmissionContext(
    "https://fictional.cloudflareaccess.com", "fictional-audience", "https://studio.example.test"
)


class CloudflareFixture:
    def __init__(self) -> None:
        self.members = {"existing@example.test"}
        self.requests: list[httpx.Request] = []
        self.mfa = True
        self.complex_group = False
        self.fail_after_write = False
        self.reject_write = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.cloudflare.com"
        assert request.headers["authorization"] == "Bearer fictional-token"
        self.requests.append(request)
        result: dict[str, object]
        if request.url.path.endswith("/organizations"):
            result = {"auth_domain": "fictional.cloudflareaccess.com"}
        elif request.url.path.endswith("/apps/" + APP):
            result = {
                "id": APP,
                "aud": CONTEXT.audience,
                "domain": "studio.example.test",
                "type": "self_hosted",
            }
        elif request.url.path.endswith("/policies/" + POLICY):
            result = {
                "id": POLICY,
                "decision": "allow",
                "include": [{"group": {"id": GROUP}}],
                "require": [{"auth_method": {"auth_method": "mfa"}}] if self.mfa else [],
            }
        else:
            assert request.url.path.endswith("/groups/" + GROUP)
            if request.method == "PUT":
                assert request.headers["if-match"] == '"fixture-version"'
                assert request.headers["x-idempotency-key"] == "fixture-operation"
                if self.reject_write:
                    return httpx.Response(412, json={"success": False, "secret": "fixture-secret"})
                payload = json.loads(request.content)
                assert set(payload) == {"name", "include", "exclude", "require"}
                assert payload["require"] == payload["exclude"] == []
                self.members = {item["email"]["email"] for item in payload["include"]}
                if self.fail_after_write:
                    self.fail_after_write = False
                    raise httpx.ReadTimeout("provider-secret-must-not-leak", request=request)
            result = {
                "id": GROUP,
                "name": MANAGED_GROUP_NAME,
                "require": [],
                "exclude": [],
                "include": [{"email": {"email": email}} for email in sorted(self.members)],
            }
            if self.complex_group:
                result["include"] = [{"everyone": {}}]
        return httpx.Response(
            200, json={"success": True, "result": result}, headers={"ETag": '"fixture-version"'}
        )

    def adapter(self, context: AdmissionContext = CONTEXT) -> CloudflareGroupAdmission:
        return CloudflareGroupAdmission(
            context,
            account=ACCOUNT,
            application=APP,
            policy=POLICY,
            group=GROUP,
            token="fictional-token",
            transport=httpx.MockTransport(self.handle),
        )


def test_adapter_preserves_group_neighbors_and_never_changes_policies() -> None:
    fixture = CloudflareFixture()
    adapter = fixture.adapter()
    adapter.reconcile("guest@example.test", True, "fixture-operation", timeout=10)
    assert fixture.members == {"guest@example.test", "existing@example.test"}
    adapter.reconcile("guest@example.test", True, "fixture-operation", timeout=10)
    assert sum(request.method == "PUT" for request in fixture.requests) == 1
    adapter.reconcile("guest@example.test", False, "fixture-operation", timeout=10)
    assert fixture.members == {"existing@example.test"}
    assert all(
        request.method == "GET" or "/access/groups/" in request.url.path
        for request in fixture.requests
    )


@pytest.mark.parametrize(
    "mismatch", ["issuer", "audience", "origin", "mfa", "selectors", "conditional"]
)
def test_adapter_rejects_mismatched_identity_policy_or_group(mismatch: str) -> None:
    fixture = CloudflareFixture()
    context = CONTEXT
    if mismatch == "issuer":
        context = replace(CONTEXT, issuer="https://different.cloudflareaccess.com")
    elif mismatch == "audience":
        context = replace(CONTEXT, audience="different-audience")
    elif mismatch == "origin":
        context = replace(CONTEXT, public_url="https://different.example.test")
    elif mismatch == "mfa":
        fixture.mfa = False
    elif mismatch == "selectors":
        fixture.complex_group = True
    else:
        fixture.reject_write = True
    with pytest.raises(AdmissionUnavailable) as error:
        fixture.adapter(context).reconcile(
            "guest@example.test", True, "fixture-operation", timeout=10
        )
    assert "secret" not in str(error.value)
    assert fixture.members == {"existing@example.test"}
    if mismatch != "conditional":
        assert all(request.method == "GET" for request in fixture.requests)


def test_adapter_recovers_uncertain_write_without_duplicate_mutation() -> None:
    fixture = CloudflareFixture()
    fixture.fail_after_write = True
    adapter = fixture.adapter()
    with pytest.raises(AdmissionUnavailable) as error:
        adapter.reconcile("guest@example.test", True, "fixture-operation", timeout=10)
    assert "secret" not in str(error.value)
    assert fixture.members == {"existing@example.test", "guest@example.test"}
    adapter.reconcile("guest@example.test", True, "fixture-operation", timeout=10)
    assert sum(request.method == "PUT" for request in fixture.requests) == 1


def test_adapter_timeout_does_not_make_a_request() -> None:
    fixture = CloudflareFixture()
    with pytest.raises(AdmissionUnavailable):
        fixture.adapter().reconcile("guest@example.test", True, "fixture-operation", timeout=0)
    assert not fixture.requests
