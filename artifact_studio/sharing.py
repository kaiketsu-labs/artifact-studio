"""Prepare immutable, audience-facing files for local sharing."""

import re
from dataclasses import dataclass
from io import BytesIO
from mimetypes import guess_type
from pathlib import PurePosixPath
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as BrowserTimeoutError
from playwright.sync_api import sync_playwright

from .artifact_models import ArtifactView
from .artifacts import ArtifactStore
from .projects import audience_html
from .renderer_health import check_renderer_available, sandboxed_browser
from .storage import ConflictError, Store
from .visuals import render_slot

ShareFormat = Literal["html", "pdf"]


@dataclass(frozen=True)
class PreparedShare:
    """A complete immutable attachment and the artifact revisions it represents."""

    filename: str
    media_type: str
    data: bytes
    revisions: tuple[tuple[str, int], ...]
    excluded: tuple[str, ...] = ()
    bundle: bool = False


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


def _audience_files(files: dict[str, bytes]) -> tuple[dict[str, bytes], tuple[str, ...]]:
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


def _pdf(html: str) -> bytes:
    """Render the local print document as a real PDF without external requests."""
    try:
        with render_slot(), sandboxed_browser(sync_playwright) as browser:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.wait_for_function("document.documentElement.dataset.printReady === 'true'")
            return page.pdf(print_background=True, prefer_css_page_size=True)
    except BrowserTimeoutError as exc:
        raise ValueError(
            "The PDF did not finish rendering. Check the interactive content."
        ) from exc
    except BrowserError as exc:
        raise ValueError(
            "PDF export needs the local Chromium renderer. Install it with "
            "uv run playwright install chromium --only-shell."
        ) from exc


class SharingService:
    """Capture one artifact or a project as an immutable audience attachment."""

    def __init__(self, store: Store, artifacts: ArtifactStore) -> None:
        """Use the existing presentation and artifact stores as export sources."""
        self.store = store
        self.artifacts = artifacts

    def _prepare(self, artifact: ArtifactView, format: ShareFormat | None) -> PreparedShare:
        """Capture one selected revision, requiring a format for presentations."""
        if artifact.legacy_deck_id:
            if format not in {"html", "pdf"}:
                raise ValueError("Choose HTML or PDF for presentations.")
            if format == "pdf":
                check_renderer_available()
            document = audience_html(self.store, artifact.legacy_deck_id, printing=format == "pdf")
            current = self.artifacts.get_artifact(artifact.id)
            if current.content_hash != artifact.content_hash:
                raise ConflictError("Presentation changed while preparing the export; try again.")
            data = document.encode() if format == "html" else _pdf(document)
            return PreparedShare(
                f"{_filename(artifact.title)}.{format}",
                "text/html" if format == "html" else "application/pdf",
                data,
                ((artifact.id, artifact.revision),),
            )
        files, excluded = _audience_files(
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
        self, artifact_id: str, format: ShareFormat | None = None
    ) -> PreparedShare:
        """Prepare the current artifact revision for download or an email draft."""
        return self._prepare(self.artifacts.get_artifact(artifact_id), format)

    def prepare_project(self, project_id: str, format: ShareFormat | None = None) -> PreparedShare:
        """Prepare a ZIP containing audience exports of all project artifacts."""
        artifacts = self.artifacts.list_artifacts(project_id)
        if not artifacts:
            raise ValueError("This project has no artifacts to share.")
        prepared = [self._prepare(artifact, format) for artifact in artifacts]
        project = next(item for item in self.artifacts.list_projects() if item.id == project_id)
        files: dict[str, bytes] = {}
        excluded: list[str] = []
        for index, (artifact, item) in enumerate(zip(artifacts, prepared, strict=True), 1):
            folder = f"{index:02d}-{_filename(artifact.title)}"
            if item.bundle:
                with ZipFile(BytesIO(item.data)) as bundle:
                    files.update(
                        {f"{folder}/{name}": bundle.read(name) for name in bundle.namelist()}
                    )
            else:
                files[f"{folder}/{item.filename}"] = item.data
            excluded.extend(f"{folder}/{path}" for path in item.excluded)
        return PreparedShare(
            f"{_filename(project.title)}.zip",
            "application/zip",
            _archive(files),
            tuple(revision for item in prepared for revision in item.revisions),
            tuple(excluded),
        )
