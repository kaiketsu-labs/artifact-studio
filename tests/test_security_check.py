"""Security setup reports separate configured controls from external guarantees."""

import json
from pathlib import Path

import pytest

from artifact_studio.deployment import DeploymentConfig
from artifact_studio.security_check import main, security_report


def hosted(preview: str = "https://previews.example.com") -> DeploymentConfig:
    return DeploymentConfig(
        mode="hosted",
        public_url="https://studio.example.com",
        preview_url=preview,
        auth_provider="cloudflare",
        auth_options={"private_option": "never-print-this"},
        member_emails=("private-member@example.com",),
        agent_subjects=("private-client-id",),
    )


def test_report_does_not_imply_remote_security_or_expose_identity_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARTIFACT_STUDIO_HOSTED_CONFIG", raising=False)
    report = security_report(hosted())
    assert report.summary == "review"
    statuses = {check.id: check.status for check in report.checks}
    assert statuses["authentication"] == "pass"
    assert statuses["mfa"] == "unverified"
    assert statuses["origin_network"] == "unverified"
    assert statuses["preview_credentials"] == "warning"
    output = report.model_dump_json()
    for secret in ("never-print-this", "private-member", "private-client-id"):
        assert secret not in output
    unrelated = security_report(hosted("https://preview.unrelated.net"))
    assert next(c for c in unrelated.checks if c.id == "preview_credentials").status == "unverified"


def test_configuration_file_permissions_are_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hosted.json"
    path.write_text("private settings")
    path.chmod(0o644)
    monkeypatch.setenv("ARTIFACT_STUDIO_HOSTED_CONFIG", str(path))
    checks = security_report(hosted()).checks
    assert any(c.id == "config_readable" and c.status == "warning" for c in checks)
    path.chmod(0o640)
    assert not any(c.id == "config_readable" for c in security_report(hosted()).checks)
    path.unlink()
    assert any(c.id == "config_changed" for c in security_report(hosted()).checks)


def test_cli_blocks_unsafe_mode_and_redacts_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["security_check", "--json"])
    monkeypatch.setenv("ARTIFACT_STUDIO_MODE", "mistake")
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    monkeypatch.setattr(DeploymentConfig, "from_env", lambda: hosted())

    def broken_provider(_config: DeploymentConfig) -> None:
        raise ValueError("never-print-this credential")

    monkeypatch.setattr("artifact_studio.security_check.load_auth_provider", broken_provider)
    assert main() == 2
    output = capsys.readouterr().out
    assert "never-print-this" not in output
    assert json.loads(output)["status"] == "blocked"


def test_local_mode_does_not_claim_hosted_authentication() -> None:
    report = security_report(DeploymentConfig())
    assert report.summary == "local"
    assert report.checks == []
