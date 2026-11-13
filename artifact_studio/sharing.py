"""Prepare immutable, audience-facing files for local sharing."""

import base64
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from io import BytesIO
from mimetypes import guess_type
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast
from zipfile import ZIP_DEFLATED, ZipFile

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .projects import audience_html
from .renderer_health import check_renderer_available, sandboxed_browser
from .storage import ConflictError, Store
from .visuals import ScopedDeckEmbedder, render_slot

ShareFormat = Literal["html", "pdf"]


class PrintProtocol(Protocol):
    """The dictionary-based Chrome DevTools interface used for streamed printing."""

    def send(self, method: str, params: dict[str, object]) -> dict[str, object]:
        """Send a print or stream operation."""
        ...


@dataclass(frozen=True)
class PreparedShare:
    """A complete immutable attachment and the artifact revisions it represents."""

    filename: str
    media_type: str
    data: bytes
    revisions: tuple[tuple[str, int], ...]
    excluded: tuple[str, ...] = ()
    bundle: bool = False


@dataclass(frozen=True)
class ShareSelection:
    """The exact revisions selected while access to the source is checked."""

    artifacts: tuple[ArtifactView, ...]
    project_title: str | None = None


def _filename(title: str) -> str:
    """Create a readable portable filename without allowing path separators."""
    cleaned = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip(".-_")
    return cleaned[:100] or "artifact"


def _archive(files: dict[str, bytes]) -> bytes:
    """Build a ZIP entirely in memory from already captured audience bytes."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return output.getvalue()


def audience_files(files: dict[str, bytes]) -> tuple[dict[str, bytes], tuple[str, ...]]:
    """Exclude recognized private metadata and reject references to excluded files."""
    blocked = {
        "package.json",
        "package-lock.json",
        "artifact.json",
        "project.json",
        "config.json",
        "settings.json",
        "secrets.json",
        "review.sqlite3",
        "review.db",
    }
    private_dirs = {"node_modules", "revisions", "review", "reviews", "notes", "chats"}
    included: dict[str, bytes] = {}
    excluded: list[str] = []
    for path, data in files.items():
        parts = PurePosixPath(path).parts
        if PurePosixPath(path).name.lower() in blocked or any(
            part.startswith(".") or part.lower() in private_dirs for part in parts
        ):
            excluded.append(path)
        else:
            included[path] = data
    for path in excluded:
        name = PurePosixPath(path).name.encode()
        if any(name in data for data in included.values()):
            raise ValueError(f"A shared file references excluded private metadata: {path}")
    return included, tuple(sorted(excluded))


@contextmanager
def _print_page(html: str) -> Generator[Page]:
    """Open a print-ready page without external requests."""
    try:
        with sandboxed_browser(sync_playwright) as browser:
            page = browser.new_page(offline=True, service_workers="block", accept_downloads=False)
            page.route("**/*", lambda route: route.abort())
            page.route_web_socket("**/*", lambda socket: socket.close())
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.wait_for_function("document.documentElement.dataset.printReady === 'true'")
            yield page
    except BrowserTimeoutError as exc:
        raise ValueError(
            "The PDF did not finish rendering. Check the interactive content."
        ) from exc
    except BrowserError as exc:
        raise ValueError(
            "PDF export needs the local Chromium renderer. Install it with "
            "uv run playwright install chromium --only-shell."
        ) from exc


def _pdf(html: str) -> bytes:
    """Render a local export in the shared renderer queue."""
    with render_slot(), _print_page(html) as page:
        return page.pdf(print_background=True, prefer_css_page_size=True)


def write_pdf_document(html: str, output: Path, limit: int) -> None:
    """Stream bounded print bytes from Chromium without materializing the complete PDF."""
    with _print_page(html) as page, output.open("wb") as target:
        cdp = cast(PrintProtocol, page.context.new_cdp_session(page))
        result = cdp.send(
            "Page.printToPDF",
            {"printBackground": True, "preferCSSPageSize": True, "transferMode": "ReturnAsStream"},
        )
        handle = cast(str, result["stream"])
        try:
            while True:
                chunk = cdp.send("IO.read", {"handle": handle, "size": 1024 * 1024})
                data = cast(str, chunk["data"])
                decoded = base64.b64decode(data) if chunk.get("base64Encoded") else data.encode()
                if target.tell() + len(decoded) > limit:
                    raise ValueError("PDF exceeds the export size limit.")
                _ = target.write(decoded)
                if chunk["eof"]:
                    break
        finally:
            _ = cdp.send("IO.close", {"handle": handle})


def _scoped_pdf(html: str, limit: int, deadline: float | None = None) -> bytes:
    """Bound hosted print jobs with a terminable worker and a capped result file."""
    source = html.encode()
    if len(source) > limit:
        raise ValueError("The print document exceeds 100 MB. Reduce the presentation assets.")
    with render_slot(), tempfile.TemporaryDirectory(prefix="artifact-studio-pdf-") as folder:
        timeout = min(45, deadline - time.monotonic()) if deadline is not None else 45
        if timeout <= 0:
            raise ValueError("The export timed out. Reduce the selection or use HTML.")
        document = Path(folder) / "print.html"
        output = Path(folder) / "audience.pdf"
        _ = document.write_bytes(source)
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "artifact_studio.share_pdf_worker",
                str(document),
                str(output),
                str(limit),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                status = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise ValueError(
                    "PDF rendering timed out. Reduce the assets or use HTML."
                ) from error
            finally:
                if process.returncode != 0:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    _ = process.wait()
        if status or not output.is_file():
            raise ValueError("The PDF could not be rendered. Check the presentation or use HTML.")
        if output.stat().st_size > limit:
            raise ValueError("Share files up to 100 MB. Export a smaller selection.")
        return output.read_bytes()


class SharingService:
    """Capture one artifact or a project as an immutable audience attachment."""

    def __init__(
        self, store: Store, artifacts: ArtifactStore, *, scoped_limit: int | None = None
    ) -> None:
        """Use the existing presentation and artifact stores as export sources."""
        self.store = store
        self.artifacts = artifacts
        self.scoped_limit = scoped_limit

    def _prepare(
        self,
        artifact: ArtifactView,
        format: ShareFormat | None,
        deadline: float | None = None,
        *,
        include_hidden: bool = True,
        slide_ids: list[str] | None = None,
    ) -> PreparedShare:
        """Capture one selected revision, requiring a format for presentations."""
        if artifact.legacy_deck_id:
            if format not in {"html", "pdf"}:
                raise ValueError("Choose HTML or PDF for presentations.")
            if format == "pdf":
                check_renderer_available()
            if self.scoped_limit is None:
                document = audience_html(
                    self.store,
                    artifact.legacy_deck_id,
                    printing=format == "pdf",
                    include_hidden=include_hidden,
                    slide_ids=slide_ids,
                )
            else:
                document = audience_html(
                    self.store,
                    artifact.legacy_deck_id,
                    printing=format == "pdf",
                    include_hidden=include_hidden,
                    scoped_embedder=ScopedDeckEmbedder(self.store, artifact.legacy_deck_id),
                    slide_ids=slide_ids,
                )
            current = self.artifacts.get_artifact(artifact.id)
            if current.content_hash != artifact.content_hash or (
                self.scoped_limit is not None
                and current.presentation_versions != artifact.presentation_versions
            ):
                raise ConflictError("Presentation changed while preparing the export; try again.")
            if format == "html":
                data = document.encode()
            elif self.scoped_limit is None:
                data = _pdf(document)
            else:
                data = _scoped_pdf(document, self.scoped_limit, deadline)
            return PreparedShare(
                f"{_filename(artifact.title)}.{format}",
                "text/html" if format == "html" else "application/pdf",
                data,
                ((artifact.id, artifact.revision),),
            )
        if slide_ids is not None:
            raise ValueError("Slide selection requires a presentation.")
        files, excluded = audience_files(
            self.artifacts.snapshot_files(artifact.id, artifact.revision)
        )
        if artifact.entrypoint not in files:
            raise ValueError("The artifact entrypoint is not an audience-shareable web file.")
        if len(files) == 1:
            return PreparedShare(
                f"{_filename(artifact.title)}{PurePosixPath(artifact.entrypoint).suffix}",
                guess_type(artifact.entrypoint)[0] or "application/octet-stream",
                files[artifact.entrypoint],
                ((artifact.id, artifact.revision),),
                excluded,
            )
        return PreparedShare(
            f"{_filename(artifact.title)}.zip",
            "application/zip",
            _archive(files),
            ((artifact.id, artifact.revision),),
            excluded,
            bundle=True,
        )

    def prepare_artifact(
        self, artifact_id: str, format: ShareFormat | None = None, *, include_hidden: bool = True
    ) -> PreparedShare:
        """Prepare the current artifact revision for download or an email draft."""
        return self.prepare(
            self.select("artifact", artifact_id), format, include_hidden=include_hidden
        )

    def select(self, scope: Literal["artifact", "project"], identifier: str) -> ShareSelection:
        """Resolve the current revision set once before copying any audience bytes."""
        if scope == "artifact":
            return ShareSelection((self.artifacts.get_artifact(identifier),))
        artifacts = self.artifacts.list_artifacts(identifier)
        if not artifacts:
            raise ValueError("This project has no artifacts to share.")
        project = next(item for item in self.artifacts.list_projects() if item.id == identifier)
        return ShareSelection(tuple(artifacts), project.title)

    def _write_project_artifact(
        self,
        archive: ZipFile,
        folder: str,
        artifact: ArtifactView,
        format: ShareFormat | None,
        deadline: float | None = None,
        *,
        include_hidden: bool = True,
    ) -> tuple[str, ...]:
        """Check and write one artifact, releasing its captured bytes before the next."""
        if artifact.legacy_deck_id:
            item = self._prepare(artifact, format, deadline, include_hidden=include_hidden)
            archive.writestr(f"{folder}/{item.filename}", item.data)
            return item.excluded
        files, excluded = audience_files(
            self.artifacts.snapshot_files(artifact.id, artifact.revision)
        )
        if artifact.entrypoint not in files:
            raise ValueError("The artifact entrypoint is not an audience-shareable web file.")
        if len(files) == 1:
            filename = f"{_filename(artifact.title)}{PurePosixPath(artifact.entrypoint).suffix}"
            archive.writestr(f"{folder}/{filename}", files[artifact.entrypoint])
        else:
            for name in sorted(files):
                archive.writestr(f"{folder}/{name}", files[name])
        return excluded

    def prepare_project(
        self, project_id: str, format: ShareFormat | None = None, *, include_hidden: bool = True
    ) -> PreparedShare:
        """Prepare a ZIP containing audience exports of all project artifacts."""
        return self.prepare(
            self.select("project", project_id), format, include_hidden=include_hidden
        )

    def prepare(
        self,
        selection: ShareSelection,
        format: ShareFormat | None = None,
        *,
        include_hidden: bool = True,
        slide_ids: list[str] | None = None,
    ) -> PreparedShare:
        """Build a single file or archive from the selected immutable revisions."""
        artifacts = selection.artifacts
        deadline = time.monotonic() + 75 if self.scoped_limit is not None else None
        if self.scoped_limit is not None and (
            sum(file.size for artifact in artifacts for file in artifact.files) > self.scoped_limit
            or any(not artifact.files_complete for artifact in artifacts)
        ):
            raise ValueError("Share files up to 100 MB. Export a smaller selection.")
        if selection.project_title is None:
            return self._prepare(
                artifacts[0], format, deadline, include_hidden=include_hidden, slide_ids=slide_ids
            )
        if slide_ids is not None:
            raise ValueError("Slide selection requires one presentation.")
        members = [
            (f"{index:02d}-{_filename(artifact.title)}", artifact)
            for index, artifact in enumerate(artifacts, 1)
        ]
        excluded: dict[str, tuple[str, ...]] = {}
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            # Preserve the original full-path ordering, including indices above 99.
            for folder, artifact in sorted(members, key=lambda item: item[0]):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("The export timed out. Reduce the selection or use HTML.")
                excluded[folder] = self._write_project_artifact(
                    archive, folder, artifact, format, deadline, include_hidden=include_hidden
                )
                if self.scoped_limit is not None and output.tell() > self.scoped_limit:
                    raise ValueError("Share files up to 100 MB. Export a smaller selection.")
        return PreparedShare(
            f"{_filename(selection.project_title)}.zip",
            "application/zip",
            output.getvalue(),
            tuple((artifact.id, artifact.revision) for artifact in artifacts),
            tuple(f"{folder}/{path}" for folder, _ in members for path in excluded[folder]),
        )
