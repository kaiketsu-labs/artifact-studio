"""Upload a local design reference to an explicitly selected hosted workspace."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from ..local_http import NoRedirect, validate_hook_url

MAX_FILE = 25_000_000
MAX_RECEIPT = 1_048_576


def read_file(path: Path) -> bytes:
    """Read one stable regular file, refusing symlinks and oversized inputs."""
    name = path.name
    if name in {"", ".", ".."} or "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Choose a file with a valid basename.")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Choose a regular file, not a symlink or directory.")
        if before.st_size > MAX_FILE:
            raise ValueError("Keep the file at or below 25 MB.")
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(
                opened.st_mode
            ):
                raise ValueError("The file changed while opening; try again.")
            data = source.read(MAX_FILE + 1)
            after = os.fstat(source.fileno())
        if len(data) > MAX_FILE:
            raise ValueError("Keep the file at or below 25 MB.")
        if (
            opened.st_size != len(data)
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("The file changed while reading; try again.")
        return data
    except OSError as exc:
        raise ValueError("Could not read the file; check its path and permissions.") from exc


def upload(
    url: str, design_id: str, path: Path, expected_sha256: str | None = None
) -> dict[str, object]:
    """Send bytes once and return the server's bounded receipt."""
    try:
        validate_hook_url(url)
        if urlsplit(url).scheme != "https":
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "Use a canonical HTTPS workspace origin without credentials, path, query or fragment."
        ) from exc
    if (
        not design_id
        or design_id in {".", ".."}
        or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in design_id)
    ):
        raise ValueError("Choose a valid design ID.")
    token = os.environ.get("ARTIFACT_STUDIO_AGENT_TOKEN", "")
    if not 32 <= len(token) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Set a valid ARTIFACT_STUDIO_AGENT_TOKEN in the environment.")
    if expected_sha256 is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("The SHA-256 guard must be 64 hexadecimal characters.")
    data = read_file(path)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256.lower():
        raise ValueError("The file does not match the SHA-256 guard.")
    endpoint = (
        url.rstrip("/")
        + "/api/agent/designs/"
        + quote(design_id, safe="")
        + "/references?"
        + urlencode({"name": path.name})
    )
    request = Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/octet-stream", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            raw = response.read(MAX_RECEIPT + 1)
        if len(raw) > MAX_RECEIPT:
            raise ValueError("The server receipt is too large.")
        receipt: object = json.loads(raw)
        if not isinstance(receipt, dict):
            raise ValueError("The server returned an invalid receipt.")
        values = cast("dict[str, object]", receipt)
        references = values.get("references")
        if not isinstance(values.get("design_id"), str) or not isinstance(references, list):
            raise ValueError("The server returned an invalid receipt.")
        cleaned: list[dict[str, object]] = []
        for item in cast("list[object]", references):
            if not isinstance(item, dict):
                raise ValueError("The server returned an invalid receipt.")
            reference = cast("dict[str, object]", item)
            keys = ("name", "path", "size", "sha256", "url", "version")
            if any(key not in reference for key in keys):
                raise ValueError("The server returned an invalid receipt.")
            cleaned.append({key: reference[key] for key in keys})
        return {"design_id": values["design_id"], "references": cleaned}
    except HTTPError as exc:
        raise ValueError(
            f"Upload failed with HTTP {exc.code}; check the destination and access."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(
            "Could not reach the workspace; check the destination and connection."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The server returned an invalid receipt.") from exc


def main() -> int:
    """Run the explicit file-backed upload command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    try:
        receipt = upload(args.url, args.design_id, args.file, args.sha256)
    except ValueError as exc:
        print(f"Upload: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
