"""Compact authoring pointers and explicit reads of human-pinned slide snapshots."""

import base64
import json
from typing import Literal

from mcp.types import CallToolResult, ImageContent, TextContent

from ..storage import ConflictError, Store
from .pinned import DesignReferences, PinnedSlide

ReferenceTarget = Literal["deck", "project"]
ReferenceView = Literal["source", "thumbnail"]


def pinned_context(
    pin: PinnedSlide | None, target: ReferenceTarget, target_id: str
) -> dict[str, object]:
    """Describe how to fetch only the reference that this authoring context resolved."""
    if pin is None:
        return {}
    return {
        "slide_reference": {
            **pin.model_dump(),
            "read_tool": "read_slide_reference",
            "arguments": {"target": target, "target_id": target_id, "pin_id": pin.pin_id},
            "views": ["thumbnail", "source"],
        }
    }


def example_context(store: Store, folder_id: str) -> dict[str, object]:
    """Suggest a visible body slide from a registered, accessible design pack."""
    folder = store.folder(folder_id)
    if (store.root / folder_id).is_symlink() or (folder / "index.html").is_symlink():
        return {}
    try:
        deck = store.read(folder_id)
    except FileNotFoundError, ValueError:
        return {}
    body = [slide for slide in deck.slides[1:] if not slide.hidden]
    if not body:
        return {}
    approvals, _ = store.review_hashes(folder_id)
    approved_candidates = [slide for slide in body if slide.id in approvals]
    approved = None
    if approved_candidates:
        theme_hash = store.visual_hash(folder_id)
        approved = next(
            (
                slide
                for slide in approved_candidates
                if store.content_hash(folder_id, slide, design_hash=theme_hash)
                == approvals[slide.id]
            ),
            None,
        )
    slide = approved or body[0]
    args = {"deck_id": folder_id, "slide_id": slide.id}
    return {
        "slide_reference": {
            "scope": "design_example",
            "title": slide.title,
            "approved": approved is not None,
            **({"content_hash": approvals[slide.id]} if approved is not None else {}),
            "source": {"tool": "get_slide", "arguments": args},
            "thumbnail": {
                "tool": "capture_contact_sheet",
                "arguments": {"deck_id": folder_id, "slide_ids": [slide.id], "limit": 1},
            },
        }
    }


def read_pin(
    store: Store, pin: PinnedSlide | None, pin_id: str, view: ReferenceView
) -> CallToolResult:
    """Use only a freshly resolved current pin, including for repeated cached pointers."""
    if pin is None or pin.pin_id != pin_id:
        raise ConflictError("Reference unavailable or replaced. Read the current working context.")
    refs = DesignReferences(store)
    metadata = pin.model_dump()
    if view == "source":
        metadata.update(refs.source(pin_id))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(metadata))],
            structured_content=metadata,
        )
    return CallToolResult(
        content=[
            ImageContent(
                type="image",
                mime_type="image/png",
                data=base64.b64encode(refs.thumbnail(pin_id)).decode("ascii"),
            )
        ],
        structured_content=metadata,
    )
