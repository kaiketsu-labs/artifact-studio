"""Optional, local OCR comparison of version-anchored slide pixels."""

import csv
import hashlib
import io
import math
import re
import shutil
import subprocess
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .presentation_files import ordinary_file
from .storage import Store, digest
from .visuals import CaptureOptions, Frame, VisualResult, snapshot

MAX_IMAGE_BYTES = 20_000_000
MAX_TEXT = 10_000
MAX_WORDS = 1_000
MAX_TSV_BYTES = 1_000_000
MAX_DIFFERENCES = 30


def normalized(text: str) -> str:
    """Normalize compatibility ligatures and whitespace, preserving C/O/0 and case."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def read_bounded(path: Path, limit: int) -> bytes:
    """Read one ordinary local file within a fixed allocation budget."""
    try:
        ordinary_file(path)
    except (ValueError, FileNotFoundError) as error:
        raise ValueError("Capture input is missing or unsafe; create a new capture.") from error
    if path.stat().st_size > limit:
        raise ValueError("Text review input exceeds its size limit.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Text review input exceeds its size limit.")
    return data


def capture_frame(output_root: Path, deck_id: str, slide_id: str, capture_id: str) -> Frame:
    """Resolve a registered full-resolution frame without accepting caller filesystem paths."""
    if not re.fullmatch(r"[a-f0-9]{32}", capture_id):
        raise ValueError("Use the capture_id returned by capture_slide or capture_contact_sheet.")
    folder = output_root.absolute() / capture_id
    manifest = VisualResult.model_validate_json(read_bounded(folder / "manifest.json", 512_000))
    if manifest.capture_id != capture_id or manifest.deck_id != deck_id:
        raise ValueError("The capture does not belong to this presentation.")
    frames = [frame for frame in manifest.frames if frame.slide_id == slide_id]
    if len(frames) != 1:
        raise ValueError("Choose one slide included in this capture.")
    frame = frames[0]
    if manifest.resolution != "full" or (frame.width, frame.height) != (1600, 900):
        raise ValueError("Use a full-resolution capture for text review.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", frame.slide_id) or frame.slide_number < 1:
        raise ValueError("Invalid capture frame identity.")
    path = folder / f"{frame.slide_number:03d}-{frame.slide_id}.png"
    if Path(frame.path).absolute() != path:
        raise ValueError("The capture frame path does not match its manifest identity.")
    if not re.fullmatch(r"[a-f0-9]{64}", frame.image_sha256):
        raise ValueError("This capture has no image integrity hash; create a new capture.")
    return frame


def is_stale(store: Store, deck_id: str, frame: Frame) -> bool:
    """Check source, order and visual dependencies again without running slide code."""
    if frame.stale:
        return True
    deck = store.read(deck_id)
    matching = [
        (number, slide) for number, slide in enumerate(deck.slides, 1) if slide.id == frame.slide_id
    ]
    if len(matching) != 1:
        return True
    number, slide = matching[0]
    if number != frame.slide_number or slide.hidden != frame.hidden:
        return True
    if (
        digest(slide.model_dump_json()) != frame.expected_hash
        or store.content_hash(deck_id, slide) != frame.content_hash
    ):
        return True
    options = CaptureOptions(
        stage=frame.stage, elapsed_ms=frame.elapsed_ms, reduced_motion=frame.reduced_motion
    )
    current = snapshot(store, deck_id, slide, number, options).frame
    return any(
        getattr(current, field) != getattr(frame, field)
        for field in (
            "expected_hash",
            "content_hash",
            "render_hash",
            "runtime_hash",
            "runtime_matches_deck",
        )
    )


def parse_words(tsv: str, width: int, height: int) -> list[dict[str, object]]:
    """Retain bounded recognized words, confidence and image-relative boxes."""
    words: list[dict[str, object]] = []
    count = 0
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        text = normalized(row.get("text") or "")
        if not text:
            continue
        try:
            confidence = float(row["conf"])
            left, top, box_width, box_height = (
                int(row[key]) for key in ("left", "top", "width", "height")
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("OCR returned invalid word metadata.") from error
        if (
            not math.isfinite(confidence)
            or not 0 <= confidence <= 100
            or min(left, top, box_width, box_height) < 0
            or left + box_width > width
            or top + box_height > height
        ):
            raise ValueError("OCR returned invalid word confidence or bounds.")
        count += len(text)
        if len(words) >= MAX_WORDS or count > MAX_TEXT:
            raise ValueError("OCR result exceeds the text-review limit; review a simpler slide.")
        words.append(
            {
                "text": text,
                "confidence": round(confidence, 2),
                "box": [left, top, box_width, box_height],
            }
        )
    return words


def recognize(image: bytes, language: str) -> list[dict[str, object]]:
    """Run the optional local engine on a copied PNG with bounded time and output reads."""
    executable = shutil.which("tesseract")
    if executable is None:
        raise ValueError(
            "Install Tesseract OCR and its requested language data to use check_slide_text."
        )
    try:
        with Image.open(io.BytesIO(image)) as source:
            if source.format != "PNG" or source.size != (1600, 900):
                raise ValueError("Text review requires a 1600x900 PNG capture.")
            source.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("The captured image is not a valid PNG.") from error
    with tempfile.TemporaryDirectory(prefix="artifact-text-review-") as directory:
        source_path = Path(directory) / "frame.png"
        output = Path(directory) / "words"
        _ = source_path.write_bytes(image)
        try:
            result = subprocess.run(
                [executable, str(source_path), str(output), "-l", language, "--psm", "11", "tsv"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                "Text recognition timed out after 20 seconds; inspect the image manually."
            ) from error
        if result.returncode:
            raise ValueError("OCR failed. Check Tesseract and its installed language data.")
        tsv = read_bounded(output.with_suffix(".tsv"), MAX_TSV_BYTES).decode("utf-8")
    return parse_words(tsv, 1600, 900)


def differences(
    expected: str, words: list[dict[str, object]]
) -> tuple[list[dict[str, object]], int]:
    """Flag candidate differences without silently equating visually confusable characters."""
    wanted = normalized(expected).split()
    observed = [str(word["text"]) for word in words]
    changes: list[dict[str, object]] = []
    omitted = 0
    for operation, left, right, start, end in SequenceMatcher(
        a=wanted, b=observed, autojunk=False
    ).get_opcodes():
        if operation == "equal":
            continue
        if len(changes) >= MAX_DIFFERENCES:
            omitted += 1
            continue
        changes.append(
            {
                "kind": operation,
                "expected": " ".join(wanted[left:right])[:500],
                "observed": " ".join(observed[start:end])[:500],
                "expected_word_range": [left, right],
                "observed_word_range": [start, end],
                "ocr_words": words[start : min(end, start + 20)],
                "excerpt_truncated": right - left > 20
                or end - start > 20
                or len(" ".join(wanted[left:right])) > 500
                or len(" ".join(observed[start:end])) > 500,
            }
        )
    return changes, omitted


def check_slide_text(
    store: Store,
    output_root: Path,
    deck_id: str,
    slide_id: str,
    capture_id: str,
    expected_text: str,
    language: str = "eng",
) -> dict[str, object]:
    """Compare supplied visible text with OCR from an existing, current slide capture."""
    if (
        not expected_text.strip()
        or len(expected_text) > MAX_TEXT
        or len(expected_text.split()) > MAX_WORDS
    ):
        raise ValueError(
            "Provide 1–10,000 characters and at most 1,000 words of expected visible text."
        )
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language) or len(language) > 80:
        raise ValueError("Use installed Tesseract language names, such as eng or eng+deu.")
    frame = capture_frame(output_root, deck_id, slide_id, capture_id)
    result: dict[str, object] = {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "capture_id": capture_id,
        "expected_hash": frame.expected_hash,
        "content_hash": frame.content_hash,
        "render_hash": frame.render_hash,
        "runtime_matches_deck": frame.runtime_matches_deck,
        "stage": frame.stage,
        "elapsed_ms": frame.elapsed_ms,
        "language": language,
        "engine": "tesseract",
        "human_review_required": True,
        "scope": "OCR compared with caller-supplied visible text; not proof of glyph fidelity.",
        "normalization": "Unicode NFKC and collapsed whitespace; case and C/O/0 remain distinct.",
    }
    if is_stale(store, deck_id, frame):
        return {
            **result,
            "status": "stale",
            "stale": True,
            "message": "Recapture the current slide before checking its text.",
        }
    image = read_bounded(Path(frame.path), MAX_IMAGE_BYTES)
    if hashlib.sha256(image).hexdigest() != frame.image_sha256:
        raise ValueError("The captured image changed; create a new capture before text review.")
    if shutil.which("tesseract") is None:
        return {
            **result,
            "status": "unavailable",
            "stale": False,
            "message": "Install Tesseract OCR and its language data to use check_slide_text.",
        }
    words = recognize(image, language)
    changes, omitted = differences(expected_text, words)
    stale = is_stale(store, deck_id, frame)
    if (
        hashlib.sha256(read_bounded(Path(frame.path), MAX_IMAGE_BYTES)).hexdigest()
        != frame.image_sha256
    ):
        stale = True
    comparison = "mismatches_detected" if changes else "no_mismatch_detected"
    return {
        **result,
        "status": "stale" if stale else comparison,
        "stale": stale,
        "comparison": comparison,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "word_count": len(words),
        "low_confidence_word_count": sum(float(str(word["confidence"])) < 70 for word in words),
        "mismatches": changes,
        "omitted_mismatch_count": omitted,
        "message": "Inspect pixels and candidates; OCR can miss errors or confuse correct glyphs.",
    }
