import json
from pathlib import Path

import pytest

from artifact_studio.deployment import DeploymentConfig


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hosted.json"
    path.write_text(
        json.dumps(
            {
                "public_url": "https://studio.example.com",
                "preview_url": "https://preview.example.com",
                "auth_provider": "cloudflare",
                "auth_options": {},
                "member_emails": ["member@example.com"],
                "agent_subjects": [],
            }
        )
    )
    path.chmod(0o640)
    monkeypatch.setenv("ARTIFACT_STUDIO_MODE", "hosted")
    monkeypatch.setenv("ARTIFACT_STUDIO_HOSTED_CONFIG", str(path))
    monkeypatch.setenv("ARTIFACT_STUDIO_COLLECTION", str(tmp_path / "collection"))
    return path


def test_valid_config_and_unsafe_permissions(config: Path) -> None:
    assert DeploymentConfig.from_env().mode == "hosted"
    config.chmod(0o666)
    with pytest.raises(ValueError, match="group- or world-writable"):
        DeploymentConfig.from_env()


def test_bounded_and_duplicate_json(config: Path) -> None:
    config.write_text('{"secret":"do-not-print", "secret":1}')
    with pytest.raises(ValueError, match="duplicate JSON keys") as error:
        DeploymentConfig.from_env()
    assert "do-not-print" not in str(error.value)
    config.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        DeploymentConfig.from_env()


def test_collection_and_symlink_rejected(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACT_STUDIO_COLLECTION", str(config.parent))
    with pytest.raises(ValueError, match="outside the served collection"):
        DeploymentConfig.from_env()
    monkeypatch.setenv("ARTIFACT_STUDIO_COLLECTION", str(config.parent / "other"))
    link = config.parent / "link.json"
    link.symlink_to(config)
    monkeypatch.setenv("ARTIFACT_STUDIO_HOSTED_CONFIG", str(link))
    with pytest.raises(ValueError, match="symlink"):
        DeploymentConfig.from_env()


def test_local_mode_with_hosted_config_fails(config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACT_STUDIO_MODE", "local")
    with pytest.raises(ValueError, match="mode is local"):
        DeploymentConfig.from_env()


@pytest.mark.parametrize(
    "url", ["https://example.com", "https://studio.example.com\\@evil.com", "https://a.localhost"]
)
def test_unsafe_preview_origins(config: Path, url: str) -> None:
    data = json.loads(config.read_text())
    data["preview_url"] = url
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        DeploymentConfig.from_env()


def test_invalid_origin_error_does_not_disclose_port(config: Path) -> None:
    data = json.loads(config.read_text())
    data["public_url"] = "https://studio.example.com:fictional-secret"
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid HTTPS DNS origin") as error:
        DeploymentConfig.from_env()
    assert "fictional-secret" not in str(error.value)
