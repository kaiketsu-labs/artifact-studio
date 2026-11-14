"""Loopback HTTPS edge and fictional IdP; browsers execute real cross-origin 302s."""

import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPSServer
from pathlib import Path
from typing import cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

HOSTS = ("studio.example.test", "login.example.test", "preview.example.test")
ORIGIN = "https://studio.example.test"
LOGIN = "https://login.example.test"


def certificate(directory: Path) -> tuple[str, str]:
    """Create an ephemeral self-signed certificate for these fictional hosts only."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fictional invitation fixture")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in HOSTS]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    _ = cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _ = key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return str(cert_path), str(key_path)


class FictionalEdge(ThreadingHTTPSServer):
    def __init__(self, client: TestClient, identity: str, directory: Path) -> None:
        self.client, self.identity = client, identity
        self.logged_in = False
        self.requests: list[str] = []
        self.tokens: list[str] = []
        self.redirects: list[tuple[str, str]] = []
        cert, key = certificate(directory)
        super().__init__(("127.0.0.1", 0), EdgeHandler, certfile=cert, keyfile=key)

    @property
    def resolver_rules(self) -> str:
        mappings = [f"MAP {host} 127.0.0.1:{self.server_port}" for host in HOSTS]
        return ", ".join([*mappings, "MAP * ~NOTFOUND"])


class EdgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        """Do not log request bodies, preview tickets or invitation credentials."""

    def do_GET(self) -> None:
        self.respond()

    def do_HEAD(self) -> None:
        self.respond()

    def do_POST(self) -> None:
        self.respond()

    def reply(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for name, value in (headers or {}).items():
            if name.lower() not in {"content-length", "content-encoding", "transfer-encoding"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def respond(self) -> None:
        edge = cast(FictionalEdge, self.server)
        host = self.headers.get("Host", "")
        if host not in HOSTS:
            self.reply(400)
            return
        url = f"https://{host}{self.path}"
        edge.requests.append(url)
        destination = ""
        if host == "login.example.test":
            if self.command != "GET":
                self.reply(405)
                return
            if self.path == "/complete":
                edge.logged_in = True
                destination = ORIGIN + "/invitation"
            elif self.path == "/sign-in":
                self.reply(
                    200,
                    b"<!doctype html><title>Fictional identity provider</title>"
                    b'<h1>Fictional sign-in</h1><a href="/complete">Sign in</a>',
                    {"Content-Type": "text/html"},
                )
                return
            else:
                self.reply(404)
                return
        elif not edge.logged_in and host == "studio.example.test" and self.path == "/invitation":
            destination = LOGIN + "/sign-in"
        if destination:
            edge.redirects.append((url, destination))
            self.reply(302, headers={"Location": destination})
            return
        headers = dict(self.headers.items())
        if edge.logged_in:
            headers["X-Fixture-Auth"] = edge.identity
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.command == "POST" and self.path == "/api/invitations/accept":
            edge.tokens.append(json.loads(body)["token"])
        response = edge.client.request(
            self.command, url, headers=headers, content=body, follow_redirects=False
        )
        self.reply(response.status_code, response.content, dict(response.headers))
