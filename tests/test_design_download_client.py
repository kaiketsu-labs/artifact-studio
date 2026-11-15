"""Verify guarded streaming and atomic publication of hosted files."""

import hashlib
import io
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from artifact_studio.designs import download
from artifact_studio.local_http import NoRedirect

TOKEN = "x" * 40
DATA = b"fictional reference" * 10_000
DIGEST = hashlib.sha256(DATA).hexdigest()


class Response(io.BytesIO):
    def __init__(
        self, data: bytes, length: str | None = None, content_type: str = "application/octet-stream"
    ) -> None:
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data)) if length is None else length
        self.read_sizes: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(-1 if size is None else size)
        return super().read(size)


class Opener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.request: Request | None = None

    def open(self, request: Request, timeout: int) -> Response:
        self.request = request
        assert timeout == 60
        return self.response


def install(monkeypatch: pytest.MonkeyPatch, response: Response) -> Opener:
    opener = Opener(response)

    def build(handler: object) -> Opener:
        assert isinstance(handler, NoRedirect)
        return opener

    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", TOKEN)
    monkeypatch.setattr(download, "build_opener", build)
    return opener


def test_streams_verified_file_and_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(DATA)
    opener = install(monkeypatch, response)
    target = tmp_path / "out.pptx"
    receipt = download.download(
        "https://studio.example/", "fictional", "reference/a b.pptx", DIGEST, target
    )
    assert target.read_bytes() == DATA
    assert receipt == {"name": "a b.pptx", "size": len(DATA), "sha256": DIGEST}
    assert opener.request is not None
    assert opener.request.get_method() == "GET"
    assert opener.request.get_header("Authorization") == "Bearer " + TOKEN
    assert "path=reference%2Fa+b.pptx" in opener.request.full_url
    assert max(response.read_sizes) <= 65_536
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.pptx"]


@pytest.mark.parametrize(
    "response,sha,error",
    [
        (Response(DATA[:-1], str(len(DATA))), DIGEST, "incomplete"),
        (Response(DATA), "0" * 64, "SHA-256"),
        (Response(DATA, str(download.MAX_FILE + 1)), DIGEST, "50 MB"),
        (Response(DATA, "bad"), DIGEST, "headers"),
        (Response(DATA, content_type="text/html"), DIGEST, "headers"),
        (Response(DATA, "1"), DIGEST, "length"),
    ],
)
def test_failures_leave_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: Response, sha: str, error: str
) -> None:
    install(monkeypatch, response)
    with pytest.raises(ValueError, match=error):
        download.download(
            "https://studio.example", "fictional", "reference/a.pptx", sha, tmp_path / "out"
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("symlink", [False, True])
def test_refuses_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symlink: bool
) -> None:
    install(monkeypatch, Response(DATA))
    existing = tmp_path / "out"
    if symlink:
        existing.symlink_to(tmp_path / "nonexistent")
    else:
        existing.write_bytes(b"keep")
    with pytest.raises(ValueError, match="already exists"):
        download.download(
            "https://studio.example", "fictional", "reference/a.pptx", DIGEST, existing
        )
    assert existing.is_symlink() if symlink else existing.read_bytes() == b"keep"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]


def test_rejects_redirect_without_leaking_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedOpener:
        def open(self, request: Request, timeout: int) -> Response:
            raise HTTPError(
                request.full_url, 302, "redirect", Message(), io.BytesIO(TOKEN.encode())
            )

    def build(handler: object) -> FailedOpener:
        assert isinstance(handler, NoRedirect)
        return FailedOpener()

    monkeypatch.setenv("ARTIFACT_STUDIO_AGENT_TOKEN", TOKEN)
    monkeypatch.setattr(download, "build_opener", build)
    with pytest.raises(ValueError, match="HTTP 302") as error:
        download.download(
            "https://studio.example", "fictional", "reference/a.pptx", DIGEST, tmp_path / "out"
        )
    assert TOKEN not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url,path",
    [
        ("http://localhost", "reference/a"),
        ("https://example.com?x=1", "reference/a"),
        ("https://example.com", "../secret"),
    ],
)
def test_rejects_invalid_destination_or_path(tmp_path: Path, url: str, path: str) -> None:
    with pytest.raises(ValueError):
        download.download(url, "fictional", path, DIGEST, tmp_path / "out")
