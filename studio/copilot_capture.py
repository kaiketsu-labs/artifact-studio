"""Remote slide captures from a disposable audience-only dependency allowlist."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp.server.mcpserver import Image as MCPImage
from mcp.types import CallToolResult, TextContent

from .models import AgentContext
from .projects import root_tag
from .storage import Store, digest
from .visuals import MAX_ASSET_BYTES, MAX_DEPENDENCY_BYTES, CaptureOptions, capture

PROJECT = Path(__file__).resolve().parents[1]


def audience_clone(store: Store, deck_id: str, destination: Path) -> Store:
    """Copy only explicit audience assets and sanitized canonical slides into an isolated store."""
    source = store.folder(deck_id)
    deck = store.read(deck_id).model_copy(deep=True)
    target = destination / "collection" / deck_id
    target.mkdir(parents=True)
    selected = [source / "theme.css"]
    assets = source / "assets"
    if assets.is_symlink():
        raise ValueError("Remote captures cannot use linked assets.")
    if assets.exists():
        for directory, subdirectories, filenames in assets.walk(follow_symlinks=False):
            if any((directory / name).is_symlink() for name in subdirectories):
                raise ValueError("Remote captures cannot use linked asset folders.")
            selected.extend(directory / name for name in filenames)
            if len(selected) > 2000:
                raise ValueError("Remote capture allows at most 2000 audience asset files.")
    total = 0
    for path in selected:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Remote captures require regular audience files without symlinks.")
        size = path.stat().st_size
        total += size
        if size > MAX_ASSET_BYTES or total > MAX_DEPENDENCY_BYTES:
            raise ValueError("Resize audience assets before remote capture.")
        copied = target / path.relative_to(source)
        copied.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(path, copied)
    for slide in deck.slides:
        slide.notes = ""
        slide.notes_revision = 0
        slide.agent = AgentContext()
        slide.sources = []
    shell = (PROJECT / "runtime/viewer.html").read_text()
    shell = shell.replace(root_tag(shell), root_tag((source / "index.html").read_text()), 1)
    _ = (target / "index.html").write_text(
        shell.replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
    )
    for name in ("presentation.js", "slideshow.css"):
        _ = shutil.copyfile(PROJECT / "runtime" / name, target / name)
    return Store(destination / "collection", destination / "review.sqlite3")


def capture_cloud_slide(
    store: Store, deck_id: str, slide_id: str, stage: int = -1, elapsed_ms: int = 0
) -> CallToolResult:
    """Render a full-size audience slide with its original concurrency and visual tokens."""
    if not -1 <= stage <= 50 or not 0 <= elapsed_ms <= 5000:
        raise ValueError("Use stage -1 for final or 0–50, and elapsed_ms between zero and 5000.")
    original = store.locate(store.read(deck_id), slide_id)
    original_hash = store.content_hash(deck_id, original)
    with TemporaryDirectory(prefix="slide-studio-copilot-capture-") as temporary:
        folder = Path(temporary)
        clone = audience_clone(store, deck_id, folder)
        result = capture(
            clone,
            deck_id,
            folder / "captures",
            slide_id=slide_id,
            options=CaptureOptions(stage=None if stage == -1 else stage, elapsed_ms=elapsed_ms),
        )
        current = store.locate(store.read(deck_id), slide_id)
        if store.content_hash(deck_id, current) != original_hash:
            raise ValueError("The source changed during capture; read the current slide and retry.")
        if Path(result.path).stat().st_size > 3 * 1024 * 1024:
            raise ValueError("The captured image exceeds the remote 3 MiB limit.")
        metadata = result.model_dump(exclude={"path"})
        metadata["frames"] = [
            frame.model_dump(exclude={"path"})
            | {
                "expected_hash": digest(original.model_dump_json()),
                "content_hash": original_hash,
                "runtime_matches_deck": False,
            }
            for frame in result.frames
        ]
        metadata["scope"] = (
            "Audience-only clone using trusted runtime and theme.css/assets. "
            "Private notes, feedback, other local files and physical displays are not captured."
        )
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(metadata)),
                MCPImage(path=result.path).to_image_content(),
            ],
            structured_content=metadata,
        )
