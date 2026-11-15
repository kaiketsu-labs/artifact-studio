"""Download an inventoried hosted design file with a required SHA-256 guard."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener

from ..local_http import NoRedirect, validate_hook_url

MAX_FILE = 50_000_000
CHUNK_SIZE = 65_536


def download(
    url: str, design_id: str, relative_path: str, expected_sha256: str, output: Path
) -> dict[str, object]:
    """Stream, verify, and publish a reference without overwriting a destination."""
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
    parts = relative_path.split("/")
    if (
        not relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(c) < 32 or ord(c) == 127 for c in relative_path)
    ):
        raise ValueError("Choose an inventoried relative design file path.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("The SHA-256 guard must be 64 hexadecimal characters.")
    token = os.environ.get("ARTIFACT_STUDIO_AGENT_TOKEN", "")
    if not 32 <= len(token) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Set a valid ARTIFACT_STUDIO_AGENT_TOKEN in the environment.")
    endpoint = (
        url.rstrip("/")
        + "/api/agent/designs/"
        + quote(design_id, safe="")
        + "/files?"
        + urlencode({"path": relative_path, "sha256": expected_sha256.lower()})
    )
    request = Request(endpoint, headers={"Authorization": "Bearer " + token}, method="GET")
    temporary: str | None = None
    try:
        with build_opener(NoRedirect()).open(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            length = response.headers.get("Content-Length", "")
            if content_type != "application/octet-stream" or not re.fullmatch(r"[0-9]+", length):
                raise ValueError("The server returned invalid file headers.")
            size = int(length)
            if size > MAX_FILE:
                raise ValueError("The file exceeds the 50 MB download limit.")
            digest = hashlib.sha256()
            total = 0
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=".artifact-download-", delete=False
            ) as stream:
                temporary = stream.name
                while True:
                    chunk = response.read(min(CHUNK_SIZE, MAX_FILE + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > size or total > MAX_FILE:
                        raise ValueError("The server returned an unexpected file length.")
                    digest.update(chunk)
                    stream.write(chunk)
            if total != size:
                raise ValueError("The download was incomplete; try again.")
            if digest.hexdigest() != expected_sha256.lower():
                raise ValueError("The downloaded file does not match the SHA-256 guard.")
        os.link(temporary, output)
        return {
            "name": PurePosixPath(relative_path).name,
            "size": total,
            "sha256": digest.hexdigest(),
        }
    except HTTPError as exc:
        raise ValueError(
            f"Download failed with HTTP {exc.code}; check the file and access."
        ) from exc
    except FileExistsError as exc:
        raise ValueError("The output already exists; choose a new destination.") from exc
    except (URLError, TimeoutError, OSError, HTTPException) as exc:
        raise ValueError(
            "Could not complete the download; check the connection and output folder."
        ) from exc
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main() -> int:
    """Run the explicit file-backed download command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--design-id", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = download(args.url, args.design_id, args.path, args.sha256, args.output)
    except ValueError as exc:
        print(f"Download: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
