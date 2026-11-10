"""Small, bounded cached images of retained audience content."""

import io
import tempfile
import threading
from pathlib import Path

from PIL import Image

from .artifact_capture import capture_artifact_image
from .storage import digest
from .version_history import VersionHistory
from .visuals import CaptureOptions, ScopedDeckEmbedder, capture_snapshot, snapshot

_THUMBNAILS = threading.Lock()
MAX_THUMBNAILS = 256


def history_thumbnail(
    history: VersionHistory,
    *,
    deck_id: str = "",
    slide_id: str = "",
    artifact_id: str = "",
    version_id: str,
    scoped_embedder: ScopedDeckEmbedder | None = None,
) -> Path:
    """Render an exact retained version, keeping only small PNGs in the private state cache."""
    store = history.store
    folder = store.database.parent / "history-thumbnails"
    with _THUMBNAILS:
        if artifact_id:
            if not version_id.startswith("artifact:") or not version_id[9:].isdigit():
                raise ValueError("Invalid artifact version ID.")
            artifact = history.artifacts.get_artifact_revision(artifact_id, int(version_id[9:]))
            if artifact.kind not in {"html", "markdown", "image", "svg"}:
                raise FileNotFoundError("This version has no visual preview.")
            key = digest("history-thumb-v1:" + artifact.id + artifact.content_hash)
            prepared = None
        else:
            selected = history.get_slide_version(deck_id, slide_id, version_id)
            deck = store.read(deck_id)
            number = next(i + 1 for i, slide in enumerate(deck.slides) if slide.id == slide_id)
            options = CaptureOptions(reduced_motion=True)
            prepared = snapshot(
                store, deck_id, selected.slide, number, options, scoped_embedder=scoped_embedder
            )
            key = digest("history-thumb-v1:" + prepared.frame.render_hash)
        path = folder / f"{key}.png"
        if path.is_file():
            path.touch()
            return path
        if prepared is not None:
            data = capture_snapshot(prepared, CaptureOptions(reduced_motion=True))
        else:
            with tempfile.TemporaryDirectory(prefix="history-thumbnail-") as temporary:
                capture = capture_artifact_image(
                    history.artifacts,
                    artifact_id,
                    Path(temporary),
                    width=1280,
                    height=800,
                    revision=int(version_id[9:]),
                )
                data = Path(capture.path).read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((400, 250), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as pending:
            temporary_path = Path(pending.name)
            try:
                _ = pending.write(output.getvalue())
                pending.close()
                temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)
        retained = sorted(folder.glob("*.png"), key=lambda item: item.stat().st_mtime_ns)
        for old in retained[:-MAX_THUMBNAILS]:
            old.unlink(missing_ok=True)
        return path
