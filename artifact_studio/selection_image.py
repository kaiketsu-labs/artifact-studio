"""Bounded image exports of explicitly selected, rendered objects."""

import base64
import math
from dataclasses import dataclass
from typing import Literal

from playwright.sync_api import Page
from pydantic import Field, model_validator

from .models import StrictModel

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000


class SelectionImageInput(StrictModel):
    """Keep image export bound to the displayed source and a finite canvas."""

    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    selectors: list[str] = Field(min_length=1, max_length=100)
    preview_html: str = Field(default="", max_length=5 * 1024 * 1024)
    width: int = Field(default=1600, ge=1, le=4096)
    height: int = Field(default=900, ge=1, le=30000)
    transparent: bool = True
    color_scheme: Literal["light", "dark"] = "light"
    format: Literal["png", "original"] = "png"

    @model_validator(mode="after")
    def bounded_selection(self) -> SelectionImageInput:
        """Reject malformed anchors and oversized raster surfaces before starting Chromium."""
        if any(not item or len(item) > 2000 for item in self.selectors):
            raise ValueError("Select specific objects with valid selectors.")
        if self.width * self.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image export exceeds 32 million pixels.")
        if self.format == "original" and len(self.selectors) != 1:
            raise ValueError("Select one image to save its original file.")
        return self


class ArtifactSelectionImageInput(SelectionImageInput):
    """Identify an immutable artifact revision rather than silently using its newest files."""

    revision: int = Field(ge=1)


@dataclass(frozen=True)
class SelectionImage:
    """Image bytes and a download format, without private review context."""

    data: bytes
    media_type: str = "image/png"
    filename: str = "selection.png"


class ImageBox(StrictModel):
    """Validate browser-derived coordinates before requesting a screenshot."""

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


def original_image(page: Page, selector: str) -> SelectionImage:
    """Read only the chosen image through the renderer's existing resource allowlist."""
    value: object = page.evaluate(
        """async ({selector, limit}) => {
            const image = document.querySelector(selector);
            if (!(image instanceof HTMLImageElement)) throw Error('Select an image.');
            const url = image.currentSrc || image.src;
            if (url.startsWith('data:')) return url;
            const response = await fetch(url);
            if (!response.ok) throw Error('The original image is unavailable.');
            const blob = await response.blob();
            if (blob.size > limit) throw Error('The original image exceeds 20 MB.');
            return await new Promise((resolve, reject) => {
                const reader = new FileReader(); reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(Error('The image could not be read.'));
                reader.readAsDataURL(blob);
            });
        }""",
        {"selector": selector, "limit": MAX_IMAGE_BYTES},
    )
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError("The original image is unavailable or exceeds 20 MB.")
    header, separator, encoded = value.partition(",")
    media_type = header.split(";", 1)[0].removeprefix("data:").lower()
    extensions = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/avif": "avif",
        "image/svg+xml": "svg",
    }
    if not separator or not header.endswith(";base64") or media_type not in extensions:
        raise ValueError("This image has no supported original download format.")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("The original image exceeds 20 MB.")
    return SelectionImage(data, media_type, f"image.{extensions[media_type]}")


def render_selection(page: Page, request: SelectionImageInput) -> SelectionImage:
    """Hide unselected paint without reflowing the chosen objects, then capture their union."""
    if request.format == "original":
        return original_image(page, request.selectors[0])
    value: object = page.evaluate(
        """({selectors, transparent}) => {
            document.querySelectorAll('[data-review-chrome],[data-artifact-bridge]')
                .forEach(node => node.remove());
            const selected = selectors.map(selector => {
                const matches = document.querySelectorAll(selector);
                if (matches.length !== 1 || matches[0].matches('html,body,script,style,head'))
                    throw Error('An object is no longer available. Select it again.');
                return matches[0];
            });
            const boxes = selected.map(node => node.getBoundingClientRect());
            if (boxes.some(box => !box.width || !box.height))
                throw Error('Select visible objects to export.');
            const left = Math.min(...boxes.map(box => box.left));
            const x = Math.max(0, Math.floor(left + scrollX - 2));
            const y = Math.max(0, Math.floor(Math.min(...boxes.map(box => box.top)) + scrollY - 2));
            const right = Math.ceil(Math.max(...boxes.map(box => box.right)) + scrollX + 2);
            const bottom = Math.ceil(Math.max(...boxes.map(box => box.bottom)) + scrollY + 2);
            if (transparent) {
                const nodes = [...document.querySelectorAll('*')];
                const kept = node => selected.some(root => root === node || root.contains(node));
                const visibility = nodes.map(node => kept(node)
                    ? getComputedStyle(node).visibility : 'hidden');
                nodes.forEach((node, index) =>
                    node.style?.setProperty('visibility', visibility[index], 'important'));
                for (const node of [document.documentElement, document.body]) {
                    node.style.setProperty('background', 'transparent', 'important');
                }
            }
            return {x, y, width:right-x, height:bottom-y};
        }""",
        {"selectors": request.selectors, "transparent": request.transparent},
    )
    box = ImageBox.model_validate(value)
    if not all(math.isfinite(number) for number in (box.x, box.y, box.width, box.height)):
        raise ValueError("The selection has invalid bounds.")
    if box.width * box.height > MAX_IMAGE_PIXELS:
        raise ValueError("Image export exceeds 32 million pixels.")
    data = page.screenshot(
        type="png",
        omit_background=request.transparent,
        clip={"x": box.x, "y": box.y, "width": box.width, "height": box.height},
        animations="disabled",
        timeout=15_000,
    )
    return SelectionImage(data)
