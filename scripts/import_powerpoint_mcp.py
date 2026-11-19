"""Send a local PowerPoint directly to the MCP server without putting bytes in chat."""

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Workspace base URL")
    parser.add_argument("--local", action="store_true", help="Use the local loopback MCP endpoint")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--request-id", required=True, help="Stable ID for exact retries")
    args = parser.parse_args()
    parsed = urlsplit(cast(str, args.url))
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Use a workspace base URL without credentials, query or fragment")
    if args.local:
        if parsed.scheme not in {"http", "https"} or not is_loopback(parsed.hostname):
            parser.error("--local requires an HTTP(S) loopback URL")
    elif parsed.scheme != "https":
        parser.error("Hosted imports require an HTTPS URL")
    if args.file.stat().st_size > 50_000_000:
        parser.error("PowerPoint exceeds the encoded upload limit")
    payload = {
        "title": args.title,
        "data_base64": base64.b64encode(args.file.read_bytes()).decode("ascii"),
        "request_id": args.request_id,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if args.local:
        url = args.url.rstrip("/") + "/mcp"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "import_powerpoint",
                "arguments": {
                    **payload,
                    "project_id": args.project_id,
                },
            },
        }
    else:
        token = os.environ.get("ARTIFACT_STUDIO_AGENT_TOKEN")
        if not token:
            parser.error("Set ARTIFACT_STUDIO_AGENT_TOKEN for hosted imports")
        headers["Authorization"] = "Bearer " + token
        url = (
            args.url.rstrip("/")
            + "/api/agent/projects/"
            + quote(args.project_id, safe="")
            + "/powerpoint"
        )
    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=180) as response:
        raw = response.read().decode()
        if response.headers.get_content_type() == "text/event-stream":
            data = "\n".join(
                line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:")
            )
        else:
            data = raw
        document = json.loads(data)
        print(json.dumps(document, ensure_ascii=False))
        if args.local and (document.get("error") or document.get("result", {}).get("isError")):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
