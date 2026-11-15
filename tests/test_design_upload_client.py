"""The hosted reference client sends only a bounded, explicitly selected file."""

import hashlib
import io
import json
from email.message import Message
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from artifact_studio.designs import upload

TOKEN = "x" * 40


class Opener:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.request: Request | None = None

    def open(self, request: Request, timeout: int) -> io.BytesIO:
        assert timeout == 60
        self.request = request
        return io.BytesIO(self.response)


def test_upload_request_and_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "example image.png"
    source.write_bytes(b"fictional source")
    receipt = {
        "design_id": "fictional",
        "references": [
            {
                "name": source.name,
                "path": "reference/example image.png",
                "size": 16,
                "sha256": "a" * 64,
                "url": "/file",
                "version": "b" * 64,
                "local_path": "/private",
            }
        ],
    }
    opener = Opener(json.dumps(receipt).encode())
    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", TOKEN)

    def build(_handler: object) -> Opener:
        return opener

    monkeypatch.setattr(upload, "build_opener", build)
    result = upload.upload(
        "https://studio.example/",
        "fictional",
        source,
        hashlib.sha256(b"fictional source").hexdigest(),
    )
    assert opener.request is not None
    assert (
        opener.request.full_url
        == "https://studio.example/api/agent/designs/fictional/references?name=example+image.png"
    )
    assert opener.request.data == b"fictional source"
    assert opener.request.get_header("Authorization") == "Bearer " + TOKEN
    assert opener.request.get_header("Content-type") == "application/octet-stream"
    references = cast("list[dict[str, object]]", result["references"])
    assert "local_path" not in references[0]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?x=1",
        "https://example.com#x",
    ],
)
def test_invalid_origin_does_not_open_file_or_network(url: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS workspace origin"):
        upload.upload(url, "fictional", tmp_path / "missing")


def test_rejects_symlinks_large_file_and_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", TOKEN)
    source = tmp_path / "source"
    source.write_bytes(b"content")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular file"):
        upload.upload("https://example.com", "fictional", link)
    with pytest.raises(ValueError, match="SHA-256 guard"):
        upload.upload("https://example.com", "fictional", source, "0" * 64)
    monkeypatch.setattr(upload, "MAX_FILE", 3)
    with pytest.raises(ValueError, match="50 MB"):
        upload.upload("https://example.com", "fictional", source)


def test_http_error_does_not_echo_server_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"content")
    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", TOKEN)

    class FailedOpener:
        def open(self, request: Request, timeout: int) -> io.BytesIO:
            raise HTTPError(
                request.full_url, 302, "redirect", Message(), io.BytesIO(TOKEN.encode())
            )

    def build(_handler: object) -> FailedOpener:
        return FailedOpener()

    monkeypatch.setattr(upload, "build_opener", build)
    with pytest.raises(ValueError, match="HTTP 302") as error:
        upload.upload("https://example.com", "fictional", source)
    assert TOKEN not in str(error.value)
