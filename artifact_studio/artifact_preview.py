"""Opaque-origin artifact previews with immutable, bundle-scoped dependencies."""

from html import escape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .local_http import validate_local_url

BRIDGE_PATH = "/web/artifact-bridge.js"
MARKDOWN_PATH = "/web/vendor/marked-18.1.2.js"
MARQUEE_PATH = "/web/marquee.js"
IMAGE_EXPORT_PATH = "/web/image-export.js"
READING_STYLE = (
    "html{color-scheme:light dark}body{margin:0;padding:32px;font:16px/1.6 system-ui;"
    "overflow-wrap:anywhere}main{max-width:880px;margin:auto}img,video{max-width:100%;"
    "height:auto}pre{overflow:auto;white-space:pre-wrap}table{border-collapse:collapse}"
    "td,th{border:1px solid #8993a055;padding:8px 12px}"
    "code{font-family:ui-monospace,monospace}"
)


def _origin(value: str, allowed_origin: str | None = None) -> str:
    """Normalize the actual loopback server origin without accepting an injected path."""
    if value != allowed_origin:
        validate_local_url(value)
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"}:
        raise ValueError("Preview origin must not contain a path.")
    return f"{parsed.scheme}://{parsed.netloc}"


def artifact_files_url(
    artifact_id: str, revision: int, origin: str, resource_root: str | None = None
) -> str:
    """Give nested assets and module imports a revision-stable directory URL."""
    if revision < 1:
        raise ValueError("Artifact revision must be positive.")
    artifact_path = f"/api/artifacts/{quote(artifact_id, safe='')}/revisions/{revision}/files/"
    return (resource_root if resource_root is not None else _origin(origin)) + artifact_path


def preview_headers(
    artifact_id: str, revision: int, origin: str, resource_root: str | None = None
) -> dict[str, str]:
    """Isolate scripts and restrict subresource requests to this immutable file bundle.

    The embedding page must also restrict frame-src to local artifact URLs to block
    external self-navigation. The iframe must retain sandbox="allow-scripts allow-downloads".
    """
    origin = _origin(origin, origin if resource_root is not None else None)
    files = artifact_files_url(artifact_id, revision, origin, resource_root)
    scripts = resource_root or origin
    policy = (
        "sandbox allow-scripts allow-downloads; default-src 'none'; "
        f"script-src 'unsafe-inline' 'unsafe-eval' {files} "
        f"{scripts}{BRIDGE_PATH} {scripts}{MARKDOWN_PATH} {scripts}{MARQUEE_PATH} "
        f"{scripts}{IMAGE_EXPORT_PATH}; "
        f"style-src 'unsafe-inline' {files}; img-src {files} data: blob:; "
        f"font-src {files} data:; media-src {files} data: blob:; "
        f"connect-src {files}; base-uri {files}; frame-ancestors {origin}; "
        "frame-src 'none'; worker-src 'none'; object-src 'none'; form-action 'none'"
    )
    return {
        "Content-Security-Policy": policy,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), display-capture=(), payment=(), usb=()"
        ),
        "Cache-Control": "no-store",
    }


def artifact_file_headers(
    artifact_id: str, revision: int, origin: str, resource_root: str | None = None
) -> dict[str, str]:
    """Allow opaque preview reads while sandboxing files opened as documents."""
    return {
        **preview_headers(artifact_id, revision, origin, resource_root),
        "Access-Control-Allow-Origin": "null",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Access-Control-Allow-Methods": "GET, HEAD",
    }


class _HeadLocation(HTMLParser):
    """Locate real opening tags without mistaking comments or script strings for markup."""

    def __init__(self, source: str) -> None:
        """Index source lines while leaving the authored document byte-for-byte intact."""
        super().__init__(convert_charrefs=False)
        self.head_end: int | None = None
        self.html_end: int | None = None
        self.line_offsets: list[int] = [0]
        for line in source.split("\n"):
            self.line_offsets.append(self.line_offsets[-1] + len(line) + 1)
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record only the first document and head opening positions."""
        line, column = self.getpos()
        end = self.line_offsets[line - 1] + column + len(self.get_starttag_text() or "")
        if tag == "head" and self.head_end is None:
            self.head_end = end
        elif tag == "html" and self.html_end is None:
            self.html_end = end


def _with_head(source: str, additions: str) -> str:
    """Prepend trusted preview setup without serializing or rewriting authored scripts."""
    location = _HeadLocation(source)
    if location.head_end is not None:
        return source[: location.head_end] + additions + source[location.head_end :]
    if location.html_end is not None:
        return (
            source[: location.html_end] + f"<head>{additions}</head>" + source[location.html_end :]
        )
    return f"<!doctype html><html><head>{additions}</head><body>{source}</body></html>"


def preview_document(
    store: ArtifactStore,
    artifact_id: str,
    origin: str,
    revision: int | None = None,
    resource_root: str | None = None,
) -> str:
    """Render owned HTML, Markdown, images or text without exposing project context."""
    origin = _origin(origin, origin if resource_root is not None else None)
    artifact, files = store.verified_snapshot(artifact_id, revision, paths="preview")
    return preview_document_from_source(
        artifact, origin, files.get(artifact.entrypoint, b""), resource_root
    )


def preview_document_from_source(
    artifact: ArtifactView, origin: str, data: bytes, resource_root: str | None = None
) -> str:
    """Build a preview from an already verified revision and its pinned entrypoint bytes."""
    origin = _origin(origin, origin if resource_root is not None else None)
    scripts = resource_root or origin
    if artifact.legacy_deck_id:
        raise ValueError("Use the presentation viewer for a legacy presentation.")
    files = artifact_files_url(artifact.id, artifact.revision, origin, resource_root)
    entrypoint = PurePosixPath(artifact.entrypoint)
    mime_type = next(item.mime_type for item in artifact.files if item.path == artifact.entrypoint)
    directory = entrypoint.parent.as_posix()
    base = files if directory == "." else files + quote(directory, safe="/") + "/"
    suffix = entrypoint.suffix.lower()
    format_name = "html"
    if mime_type.startswith("image/"):
        source = (
            f"<style>{READING_STYLE}body{{padding:0}}img{{display:block;margin:auto}}</style>"
            f'<img src="{escape(files + quote(artifact.entrypoint, safe="/"), quote=True)}" '
            f'alt="{escape(artifact.title, quote=True)}">'
        )
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(
                "This file has no HTML, image, Markdown or UTF-8 text preview."
            ) from error
        if mime_type == "text/html" or suffix in {".html", ".htm", ".xhtml"}:
            source = text
        elif suffix in {".md", ".markdown"} or mime_type == "text/markdown":
            format_name = "markdown"
            source = (
                f"<style>{READING_STYLE}</style>"
                f'<script src="{scripts}{MARKDOWN_PATH}" defer></script>'
                f'<pre id="artifact-markdown-source" hidden>{escape(text)}</pre>'
                '<main id="artifact-markdown"></main>'
            )
        else:
            source = f"<style>{READING_STYLE}</style><main><pre>{escape(text)}</pre></main>"
    additions = (
        '<meta charset="utf-8">'
        f'<base href="{escape(base, quote=True)}">'
        f'<script src="{scripts}{MARQUEE_PATH}"></script>'
        f'<script src="{scripts}{IMAGE_EXPORT_PATH}"></script>'
        f'<script src="{scripts}{BRIDGE_PATH}" '
        f'data-artifact-root="{escape(files, quote=True)}" '
        f'data-artifact-format="{format_name}"></script>'
    )
    return _with_head(source, additions)
