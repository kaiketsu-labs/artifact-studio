"""The fixed, public client source distributed through authenticated transports."""

import hashlib
from pathlib import Path


def client_files() -> list[dict[str, str]]:
    """Return only the two shipped client modules; no caller-supplied host paths."""
    root = Path(__file__).resolve().parent
    result: list[dict[str, str]] = []
    for name in ("hook_client.py", "local_http.py"):
        data = (root / name).read_bytes()
        result.append(
            {"path": name, "text": data.decode(), "sha256": hashlib.sha256(data).hexdigest()}
        )
    return result
