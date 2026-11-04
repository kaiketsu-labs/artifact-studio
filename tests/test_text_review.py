"""OCR candidates preserve capture integrity, version identity and uncertainty."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from artifact_studio import text_review
from artifact_studio.models import SlidePatch
from artifact_studio.storage import Store, digest
from artifact_studio.visuals import CaptureOptions, VisualResult, snapshot
from tests.design_factory import TEMPLATE_ID, make_design


def installed_engine(_name: str) -> str:
    return "/local/tesseract"


def missing_engine(_name: str) -> None:
    return None


CAPTURE_ID = "a" * 32
EXPECTED = "COCO CODE COLD"


@pytest.fixture
def captured(tmp_path: Path) -> tuple[Store, Path, Path]:
    """Save a synthetic PNG alongside real snapshot hashes without launching a browser."""
    make_design(tmp_path / "collection" / TEMPLATE_ID)
    store = Store(tmp_path / "collection", tmp_path / "review.sqlite3")
    deck = store.create("2026-11-04-text-check", "Typography", "test-brand")
    _ = store.revise(
        deck.id,
        "cover",
        digest(deck.slides[0].model_dump_json()),
        "fixture-text",
        SlidePatch(html=f'<article class="slide"><h1>{EXPECTED}</h1></article>'),
    )
    frame = snapshot(store, deck.id, store.read(deck.id).slides[0], 1, CaptureOptions()).frame
    output = tmp_path / "captures"
    folder = output / CAPTURE_ID
    folder.mkdir(parents=True)
    path = folder / "001-cover.png"
    Image.new("RGB", (1600, 900), "white").save(path)
    frame.path = str(path)
    frame.image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    result = VisualResult(
        capture_id=CAPTURE_ID,
        deck_id=deck.id,
        captured_at="2026-11-04T00:00:00Z",
        kind="slide",
        path=str(path),
        frames=[frame],
        total_slides=1,
        selected_slides=1,
    )
    _ = (folder / "manifest.json").write_text(result.model_dump_json())
    return store, output, path


def check(captured: tuple[Store, Path, Path], expected: str = EXPECTED) -> dict[str, object]:
    """Use the same public service called by MCP with an existing capture identity."""
    store, root, _ = captured
    return text_review.check_slide_text(
        store, root, "2026-11-04-text-check", "cover", CAPTURE_ID, expected
    )


def test_text_normalization_and_glyph_confusions() -> None:
    """Compatibility ligatures normalize; visually similar letters and digits stay distinct."""
    assert text_review.normalized("ﬁrst\n office  ﬂ") == "first office fl"
    words: list[dict[str, object]] = [
        {"text": "first", "confidence": 96, "box": [0, 0, 60, 20]},
        {"text": "O", "confidence": 88, "box": [70, 0, 20, 20]},
    ]
    changes, omitted = text_review.differences("ﬁrst C", words)
    assert omitted == 0 and len(changes) == 1
    assert changes[0]["expected"] == "C" and changes[0]["observed"] == "O"
    assert text_review.differences("first 0", words)[0]


def test_stale_skips_ocr_and_preserves_private_notes(
    captured: tuple[Store, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent human note never becomes a fresh match or leaks into the result."""
    store, _, _ = captured

    def forbidden(_image: bytes, _language: str) -> list[dict[str, object]]:
        pytest.fail("Stale captures must not invoke OCR.")

    monkeypatch.setattr(text_review, "recognize", forbidden)
    _ = store.save_notes("2026-11-04-text-check", "cover", "PRIVATE_NOTE", "")
    result = check(captured)
    assert result["status"] == "stale" and result["stale"] is True
    assert "PRIVATE_NOTE" not in str(result)


def test_version_changes_during_ocr_are_reported_stale(
    captured: tuple[Store, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a perfect recognized string cannot certify content modified during recognition."""
    store, _, _ = captured
    monkeypatch.setattr(text_review.shutil, "which", installed_engine)

    def concurrent(_image: bytes, _language: str) -> list[dict[str, object]]:
        _ = store.save_notes("2026-11-04-text-check", "cover", "Concurrent note", "")
        return [
            {"text": token, "confidence": 99, "box": [0, 0, 20, 20]} for token in EXPECTED.split()
        ]

    monkeypatch.setattr(text_review, "recognize", concurrent)
    result = check(captured)
    assert result["comparison"] == "no_mismatch_detected"
    assert result["status"] == "stale" and result["human_review_required"] is True


def test_unavailable_engine_and_image_integrity(
    captured: tuple[Store, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing engine is an explicit unavailable result; altered pixels require recapture."""
    monkeypatch.setattr(text_review.shutil, "which", missing_engine)
    result = check(captured)
    assert result["status"] == "unavailable" and "Install Tesseract" in str(result["message"])
    assert "comparison" not in result
    Image.new("RGB", (1600, 900), "black").save(captured[2])
    with pytest.raises(ValueError, match="image changed"):
        _ = check(captured)


def test_image_changes_during_ocr_are_stale(
    captured: tuple[Store, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes recognized and the saved image cannot silently diverge during review."""
    monkeypatch.setattr(text_review.shutil, "which", installed_engine)

    def changed(_image: bytes, _language: str) -> list[dict[str, object]]:
        Image.new("RGB", (1600, 900), "black").save(captured[2])
        return [
            {"text": token, "confidence": 99, "box": [0, 0, 20, 20]} for token in EXPECTED.split()
        ]

    monkeypatch.setattr(text_review, "recognize", changed)
    result = check(captured)
    assert result["status"] == "stale" and result["comparison"] == "no_mismatch_detected"


def test_runtime_compatibility_changes_invalidate_capture(
    captured: tuple[Store, Path, Path],
) -> None:
    """Do not carry forward an obsolete claim that the trusted renderer matches the deck."""
    store, _, _ = captured
    runtime = store.folder("2026-11-04-text-check") / "presentation.js"
    _ = runtime.write_text(runtime.read_text() + "\n// changed custom runtime\n")
    assert check(captured)["status"] == "stale"


def test_static_reveal_becoming_interactive_is_stale(
    captured: tuple[Store, Path, Path],
) -> None:
    """Check source identity before applying a former static reveal stage to a new document."""
    store, _, path = captured
    deck_id = "2026-11-04-text-check"
    current = store.read(deck_id).slides[0]
    _ = store.revise(
        deck_id,
        "cover",
        digest(current.model_dump_json()),
        "static-reveal",
        SlidePatch(html=f'<article class="slide"><h1 data-build="1">{EXPECTED}</h1></article>'),
    )
    frame = snapshot(
        store, deck_id, store.read(deck_id).slides[0], 1, CaptureOptions(stage=1)
    ).frame
    frame.path = str(path)
    frame.image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = path.parent / "manifest.json"
    manifest = VisualResult.model_validate_json(manifest_path.read_text())
    manifest.frames = [frame]
    _ = manifest_path.write_text(manifest.model_dump_json())
    current = store.read(deck_id).slides[0]
    _ = store.revise(
        deck_id,
        "cover",
        digest(current.model_dump_json()),
        "now-interactive",
        SlidePatch(interactive_html=f"<h1>{EXPECTED}</h1>"),
    )
    assert check(captured)["status"] == "stale"


def test_manifest_identity_path_and_old_hash_rejected(captured: tuple[Store, Path, Path]) -> None:
    """A capture ID cannot select another deck, arbitrary file or unanchored legacy PNG."""
    store, root, path = captured
    for bad_id in ("../private", "a" * 33, "/tmp/secret"):
        with pytest.raises(ValueError, match="capture_id"):
            _ = text_review.check_slide_text(store, root, "x", "cover", bad_id, EXPECTED)
    with pytest.raises(ValueError, match="presentation"):
        _ = text_review.check_slide_text(store, root, "another", "cover", CAPTURE_ID, EXPECTED)
    manifest_path = path.parent / "manifest.json"
    manifest = VisualResult.model_validate_json(manifest_path.read_text())
    manifest.frames[0].path = str(root.parent / "secret.png")
    _ = manifest_path.write_text(manifest.model_dump_json())
    with pytest.raises(ValueError, match="path"):
        _ = check(captured)
    manifest.frames[0].path = str(path)
    manifest.frames[0].image_sha256 = ""
    _ = manifest_path.write_text(manifest.model_dump_json())
    with pytest.raises(ValueError, match="integrity hash"):
        _ = check(captured)


def test_symlinks_and_size_limits(captured: tuple[Store, Path, Path], tmp_path: Path) -> None:
    """Read failures do not expose private absolute paths and inputs stay bounded."""
    _, _, image = captured
    data = image.read_bytes()
    image.unlink()
    elsewhere = tmp_path / "private.png"
    _ = elsewhere.write_bytes(data)
    image.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="missing or unsafe") as raised:
        _ = check(captured)
    assert str(tmp_path) not in str(raised.value)
    with pytest.raises(ValueError, match="size limit"):
        _ = text_review.read_bounded(elsewhere, 1)


def test_ocr_metadata_bounds() -> None:
    """Reject non-finite confidence, off-image boxes and excessive OCR output."""
    header = "level\tleft\ttop\twidth\theight\tconf\ttext\n"
    for row in ("5\t0\t0\t10\t10\tnan\tC", "5\t1599\t0\t50\t10\t90\tC"):
        with pytest.raises(ValueError, match="confidence or bounds"):
            _ = text_review.parse_words(header + row, 1600, 900)
    with pytest.raises(ValueError, match="result exceeds"):
        _ = text_review.parse_words(header + "5\t0\t0\t10\t10\t90\tC\n" * 1001, 1600, 900)


def test_ocr_timeout(captured: tuple[Store, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalled external engine becomes an actionable bounded failure."""
    monkeypatch.setattr(text_review.shutil, "which", installed_engine)

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("tesseract", 20)

    monkeypatch.setattr(text_review.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        _ = check(captured)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Optional local OCR engine")
def test_real_ocr_detects_controlled_glyph_changes(
    captured: tuple[Store, Path, Path],
) -> None:
    """Recognize normal pixels, then flag injected O glyphs against unchanged expected C text."""
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font_path.is_file():
        pytest.skip("Fictional OCR fixture needs DejaVu Sans")
    _, _, path = captured
    font = ImageFont.truetype(str(font_path), 76)
    for painted, status in (
        (EXPECTED, "no_mismatch_detected"),
        ("OOOO OODE OOLD", "mismatches_detected"),
    ):
        image = Image.new("RGB", (1600, 900), "white")
        ImageDraw.Draw(image).text((80, 180), painted, font=font, fill="black")
        image.save(path)
        manifest_path = path.parent / "manifest.json"
        manifest = VisualResult.model_validate_json(manifest_path.read_text())
        manifest.frames[0].image_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        _ = manifest_path.write_text(manifest.model_dump_json())
        result = check(captured)
        assert result["status"] == status and result["stale"] is False
        assert result["human_review_required"] is True
