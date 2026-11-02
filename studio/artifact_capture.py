"""Network-isolated captures of exact artifact revisions, including authored JavaScript."""

import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from PIL import Image
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Route, sync_playwright
from pydantic import Field

from .artifact_models import ArtifactView
from .artifact_preview import (
    BRIDGE_PATH,
    MARKDOWN_PATH,
    artifact_file_headers,
    artifact_files_url,
    preview_document,
    preview_headers,
)
from .artifacts import ArtifactStore
from .models import StrictModel
from .visuals import MAX_DEPENDENCY_BYTES, render_slot

CAPTURE_ORIGIN = "http://127.0.0.1:8799"
CAPTURE_URL = CAPTURE_ORIGIN + "/__artifact_capture__"
PROJECT = Path(__file__).resolve().parents[1]
MAX_CAPTURE_PIXELS = 32_000_000


@dataclass(frozen=True)
class CaptureResource:
    """Bytes and response policy pinned before any authored program runs."""

    body: bytes
    mime_type: str
    headers: dict[str, str]


class ArtifactCapture(StrictModel):
    """Identify the precise source revision, viewport and resulting local screenshot."""

    artifact_id: str
    path: str
    content_hash: str
    revision: int
    width: int
    height: int
    viewport_width: int
    viewport_height: int
    elapsed_ms: int
    full_page: bool
    captured_at: str
    stale: bool = False
    external_dependencies: list[str] = Field(default_factory=list)
    blocked_requests: list[str] = Field(default_factory=list)


def capture_resources(store: ArtifactStore, artifact: ArtifactView) -> dict[str, CaptureResource]:
    """Pin a bounded allowlist of owned snapshot bytes and the trusted preview adapters."""
    if artifact.legacy_deck_id:
        raise ValueError("Use the presentation capture for a legacy presentation.")
    if sum(item.size for item in artifact.files) > MAX_DEPENDENCY_BYTES:
        raise ValueError("Artifact capture dependencies exceed 64 MiB. Resize assets first.")
    files = store.snapshot_files(artifact.id, artifact.revision)
    base = artifact_files_url(artifact.id, artifact.revision, CAPTURE_ORIGIN)
    headers = artifact_file_headers(artifact.id, artifact.revision, CAPTURE_ORIGIN)
    resources = {
        base + quote(item.path, safe="/"): CaptureResource(
            files[item.path], item.mime_type, headers
        )
        for item in artifact.files
    }
    for name in (BRIDGE_PATH, MARKDOWN_PATH):
        resources[CAPTURE_ORIGIN + name] = CaptureResource(
            (PROJECT / name.lstrip("/")).read_bytes(), "text/javascript", headers
        )
    resources[CAPTURE_URL] = CaptureResource(
        preview_document(store, artifact.id, CAPTURE_ORIGIN, artifact.revision).encode(),
        "text/html",
        preview_headers(artifact.id, artifact.revision, CAPTURE_ORIGIN),
    )
    return resources


def capture_resource(
    resources: dict[str, CaptureResource], url: str, method: str = "GET"
) -> CaptureResource | None:
    """Resolve only exact owned URLs, allowing ordinary cache-busting query strings."""
    if method not in {"GET", "HEAD"}:
        return None
    parsed = urlsplit(url)
    normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return resources.get(normalized)


def capture_artifact_image(
    store: ArtifactStore,
    artifact_id: str,
    outputdir: Path,
    width: int = 1280,
    height: int = 900,
    elapsed_ms: int = 0,
    full_page: bool = False,
    revision: int | None = None,
) -> ArtifactCapture:
    """Run authored programs in a fresh, offline renderer with no access to a live server."""
    if not 240 <= width <= 4096 or not 240 <= height <= 4096:
        raise ValueError("Capture dimensions must be between 240 and 4096 pixels.")
    if not 0 <= elapsed_ms <= 60_000:
        raise ValueError("Capture elapsed_ms must be between 0 and 60000.")
    with render_slot():
        artifact = (
            store.get_artifact(artifact_id)
            if revision is None
            else store.get_artifact_revision(artifact_id, revision)
        )
        resources = capture_resources(store, artifact)
        blocked: set[str] = set()

        def route_request(route: Route) -> None:
            """Fulfill verified bytes and fail closed for every other request."""
            request = route.request
            resource = capture_resource(resources, request.url, request.method)
            if resource is None:
                if len(blocked) < 100:
                    blocked.add(request.url[:2000])
                route.abort()
            else:
                route.fulfill(
                    status=200,
                    body=resource.body if request.method == "GET" else b"",
                    content_type=resource.mime_type,
                    headers=resource.headers,
                )

        try:
            with sync_playwright() as renderer:
                browser = renderer.chromium.launch(headless=True, chromium_sandbox=True)
                try:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                        offline=True,
                        service_workers="block",
                        accept_downloads=False,
                    )
                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    page = context.new_page()
                    page.set_default_timeout(15_000)
                    _ = page.goto(CAPTURE_URL, wait_until="load", timeout=15_000)
                    _ = page.evaluate(
                        "async () => {await document.fonts.ready;"
                        "await Promise.all([...document.images].map(image => "
                        "image.decode().catch(() => {})));}"
                    )
                    if elapsed_ms:
                        page.wait_for_timeout(elapsed_ms)
                    if full_page:
                        pixels: object = page.evaluate(
                            "() => Math.max(document.documentElement.scrollWidth, innerWidth)"
                            " * Math.max(document.documentElement.scrollHeight, innerHeight)"
                        )
                        if not isinstance(pixels, int | float) or pixels > MAX_CAPTURE_PIXELS:
                            raise ValueError("Full-page capture exceeds 32 million pixels.")
                    data = page.screenshot(
                        type="png", full_page=full_page, animations="allow", timeout=15_000
                    )
                finally:
                    browser.close()
        except BrowserError as error:
            if "Executable doesn't exist" in str(error):
                raise ValueError(
                    "Install the local renderer: uv run playwright install chromium --only-shell"
                ) from error
            raise ValueError(f"Artifact capture failed: {str(error)[:500]}") from error
        with Image.open(io.BytesIO(data)) as captured:
            captured_width, captured_height = captured.size
        folder = outputdir.resolve() / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / "preview.png"
        _ = path.write_bytes(data)
        result = ArtifactCapture(
            artifact_id=artifact.id,
            path=str(path),
            content_hash=artifact.content_hash,
            revision=artifact.revision,
            width=captured_width,
            height=captured_height,
            viewport_width=width,
            viewport_height=height,
            elapsed_ms=elapsed_ms,
            full_page=full_page,
            captured_at=datetime.now(UTC).isoformat(),
            stale=store.get_artifact(artifact.id).content_hash != artifact.content_hash,
            external_dependencies=artifact.external_dependencies,
            blocked_requests=sorted(blocked),
        )
        _ = (folder / "capture.json").write_text(result.model_dump_json(indent=2))
        return result
