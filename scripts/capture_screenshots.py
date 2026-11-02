"""Refresh documentation from the real UI, using an isolated fictional collection.

Run from the checkout: uv run python -m scripts.capture_screenshots
Requires Playwright Chromium. No agent CLI, AI connection or user data is used.
"""

import argparse
import hashlib
import json
import re
import shutil
import struct
import tempfile
from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright

from scripts.screenshot_fixture import seed
from studio.agent_client import Invocation
from studio.models import Deck
from studio.visuals import CaptureOptions, Diagnostics, contact_sheet, snapshot
from tests.integration.support import DECK_ID, PROJECT, RunningApp, running_app


class Capture:
    """Exercise real app controls and keep image provenance alongside the captures."""

    def __init__(self, app: RunningApp, browser: Browser, output: Path):
        self.app = app
        self.browser = browser
        self.output = output
        self.images: dict[str, dict[str, object]] = {}
        self.errors: list[str] = []
        self.context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            device_scale_factor=1,
            color_scheme="light",
            reduced_motion="reduce",
            locale="en-GB",
        )
        self.context.on(
            "page", lambda page: page.on("pageerror", lambda error: self.errors.append(str(error)))
        )
        self.page = self.context.new_page()
        self.page.clock.set_fixed_time("2026-11-02T10:30:00Z")

    def open(self, slide: str = "workflow") -> None:
        """Wait for canonical slide context, fonts and the default-open notes."""
        self.page.goto(self.app.url + "/?deck=" + DECK_ID)
        self.page.locator(f'.slide-thumb[data-id="{slide}"]').click()
        expect(self.page.locator("#notes-panel")).to_have_attribute("open", "")
        expect(self.page.frame_locator("#preview").locator(".slide")).to_be_visible()

    def save(self, name: str, page: Page | None = None, region: str | None = None) -> None:
        """Capture actual pixels, with no DOM replacement or screenshot retouching."""
        page = page or self.page
        expect(page.locator("#toast")).to_be_hidden(timeout=10_000)
        _ = page.evaluate("document.fonts.ready")
        _ = page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )
        page.mouse.move(1435, 995)
        target = self.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if region:
            _ = page.locator(region).screenshot(path=target, animations="disabled")
        else:
            _ = page.screenshot(path=target, animations="disabled")
        self.images[name] = {"viewport": page.viewport_size, "region": region}
        print(f"Captured {name}", flush=True)

    def menu(self, label: str) -> None:
        self.page.get_by_role("button", name="Menu", exact=True).click()
        self.page.get_by_role("menuitem", name=label, exact=True).click()

    def appearance(self, value: str) -> None:
        self.menu("Appearance")
        self.page.get_by_role("menuitemradio", name=value, exact=True).click()
        expect(self.page.locator("html")).to_have_attribute("data-theme", value.lower())

    def conversation(self) -> None:
        """Submit feedback and an MCP Markdown response through real boundaries."""
        self.page.locator("#feedback-file-input").set_input_files(
            {
                "name": "review-notes.md",
                "mimeType": "text/markdown",
                "buffer": b"# Review direction\nKeep the three steps. Give Review a mint accent.\n",
            }
        )
        self.page.locator("#feedback-text").fill(
            "Keep the three-step story. Could Review have a softer accent?"
        )
        with self.page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/slides/workflow/feedback")
            )
        ) as submitted:
            self.page.locator("#send-feedback").click()
        expect(self.page.locator("#feedback-list")).to_contain_text("Keep the three-step story.")
        self.app.client().call(
            Invocation(
                tool="reply_to_feedback",
                arguments={
                    "deck_id": DECK_ID,
                    "slide_id": "workflow",
                    "event_id": int(submitted.value.json()["event_id"]),
                    "request_id": "docs-review-reply-001",
                    "message": "**Updated the review step.**\n\n- Softer mint accent\n"
                    "- Same three-step flow\n- Clear contrast for the labels\n\n"
                    "The speaker notes stay private when you present.",
                },
            )
        )
        expect(self.page.locator("#feedback-list strong")).to_contain_text(
            "Updated the review step."
        )

    def workspace(self) -> None:
        self.open()
        self.conversation()
        self.save("docs/assets/workspace.png")
        self.page.locator("#chat-resizer").focus()
        for _ in range(5):
            self.page.keyboard.press("ArrowLeft")
        self.save("docs/assets/slide-chat.png")
        self.save("docs/screenshots/chat-sender-icons-light.png")
        self.appearance("Dark")
        self.save("docs/screenshots/chat-sender-icons-dark.png")
        self.appearance("Light")
        self.page.locator("#chat-resizer").focus()
        self.page.keyboard.press("Enter")
        self.page.locator("#mode-batch").click()
        self.page.frame_locator("#preview").locator("#review-step").click(
            position={"x": 15, "y": 15}
        )
        expect(self.page.locator("#selection-box")).to_be_visible()
        self.page.locator("#feedback-text").fill("Give this step a little more breathing room.")
        self.save("docs/assets/element-review.png")
        self.page.locator("#feedback-text").fill("")
        self.page.locator("#clear-target").click()
        self.page.locator("#mode-live").click()

    def overview(self) -> None:
        self.page.locator("#sorter-view").click()
        self.page.locator('.slide-thumb[data-id="workflow"]').click()
        self.page.locator('.slide-thumb[data-id="story"]').click(modifiers=["Control"])
        self.save("docs/assets/slide-sorter.png")
        self.page.locator('.slide-thumb[data-id="workflow"]').click()
        self.page.keyboard.press("ArrowDown")
        expect(self.page.locator('.slide-thumb[data-id="ready"]')).to_have_attribute(
            "aria-current", "true"
        )
        self.save("docs/screenshots/section-sorter-navigation.png")
        self.page.keyboard.press("Escape")
        self.page.locator('.slide-thumb[data-id="workflow"]').click(button="right")
        self.save("docs/assets/slide-management.png")
        self.page.keyboard.press("Escape")

    def questions(self) -> None:
        self.page.locator('.slide-thumb[data-id="workflow"]').click()
        expect(self.page.locator("#chat-slide-title")).to_have_text(
            "Good slides happen in the loop"
        )
        slide = self.app.call("get_slide", deck_id=DECK_ID, slide_id="workflow")
        self.app.call(
            "ask_slide_question",
            deck_id=DECK_ID,
            slide_id="workflow",
            expected_hash=slide["expected_hash"],
            request_id="docs-question-001",
            question="Who should this presentation speak to?",
            options=["Leadership team", "New teammates", "Customers"],
        )
        expect(self.page.locator("#chat-question-status")).to_be_visible()
        self.page.locator("#chat-question-status").click()
        self.save("docs/screenshots/slide-question-light.png")
        self.appearance("Dark")
        self.save("docs/screenshots/slide-question-dark.png")
        self.page.locator(".section-toggle").first.click()
        self.save("docs/screenshots/slide-sections-dark.png")
        self.appearance("Light")
        self.save("docs/screenshots/slide-sections-light.png")
        self.save("docs/assets/slide-sections.png")
        self.page.locator("#add-slide").click()
        expect(self.page.locator('.slide-thumb[aria-current="true"]')).to_be_visible()
        self.save("docs/screenshots/section-active-slide.png")

    def references(self) -> None:
        self.menu("Design references")
        expect(self.page.get_by_role("dialog", name="Design references")).to_be_visible()
        self.save("docs/screenshots/design-references.png")
        self.page.locator("#design-reference-input").set_input_files(
            [
                {
                    "name": "audience-brief.md",
                    "mimeType": "text/markdown",
                    "buffer": b"# Audience\nA fictional team reviewing presentations together.\n",
                },
                {
                    "name": "palette.csv",
                    "mimeType": "text/csv",
                    "buffer": b"colour,hex\nink,#132944\nblue,#315bea\nmint,#c1efdd\n",
                },
            ]
        )
        self.save("docs/screenshots/reference-controls.png")
        self.page.get_by_role("button", name="Save references", exact=True).click()
        expect(self.page.locator("#design-reference-status")).to_have_text("References saved.")
        self.save("docs/screenshots/reference-files.png")
        self.page.get_by_role("button", name="Remove palette.csv from design context").click()
        expect(self.page.locator("#design-reference-list")).not_to_contain_text("palette.csv")
        self.save("docs/screenshots/reference-removed.png")
        self.page.locator("#close-design-references").click()
        self.menu("New presentation")
        self.page.locator("#new-title").fill("Our next chapter")
        self.page.locator("#new-brief").fill(
            "A short welcome for our new teammates. Keep the story clear and the tone warm."
        )
        self.page.get_by_role("button", name="Manage selected design references").click()
        self.save("docs/screenshots/new-presentation-references.png")
        self.page.locator("#close-design-references").click()
        self.page.keyboard.press("Escape")

    def projects(self) -> None:
        self.menu("Project files")
        self.save("docs/assets/projects.png")
        self.page.locator("#close-project").click()

    def interactive(self) -> None:
        self.page.locator('.slide-thumb[data-id="interactive"]').click()
        self.page.locator("#play-animations").click()
        frame = self.page.frame_locator(".animation-preview > iframe").frame_locator("iframe")
        frame.get_by_role("button", name="Compare", exact=True).click()
        expect(frame.locator("#chart-label")).to_contain_text("Comparison view")
        self.save("docs/assets/interactive-slide.png")
        self.page.keyboard.press("Escape")

    def presenter(self) -> None:
        page = self.context.new_page()
        page.clock.set_fixed_time("2026-11-02T10:30:00Z")
        page.goto(f"{self.app.url}/decks/{DECK_ID}/index.html?present=1#workflow")
        page.locator("#show-presenter").click()
        expect(page.locator("#presenter-view")).to_be_visible()
        page.locator("#pause-timer").click()
        page.locator("#reset-timer").click()
        expect(page.locator("#presenter-notes")).to_have_value(
            re.compile("Start with the brief\\.")
        )
        self.save("docs/assets/presenter.png", page)
        page.close()

    def visual_review(self, deck: Deck) -> None:
        """Use production sanitization, readiness and contact-sheet composition.

        Documentation browser captures do not claim production renderer OS isolation.
        The capture tool's sandbox itself is covered by its dedicated renderer tests.
        """
        page = self.context.new_page()
        page.set_viewport_size({"width": 1600, "height": 900})
        frames = []
        images = []
        observations = {}
        for index in (0, 1, 2, 5):
            item = snapshot(
                self.app.store, DECK_ID, deck.slides[index], index + 1, CaptureOptions()
            )
            page.set_content(item.document, wait_until="load")
            diagnostics = Diagnostics.model_validate(
                page.evaluate("() => window.SlideStudioCaptureReady")
            )
            if (
                diagnostics.failed_images
                or diagnostics.failed_fonts
                or diagnostics.possible_overflow
            ):
                raise RuntimeError(diagnostics.model_dump())
            observations[item.frame.slide_id] = diagnostics.model_dump()
            frames.append(item.frame)
            images.append(page.screenshot(type="png", animations="disabled"))
        name = "docs/assets/visual-review.png"
        _ = (self.output / name).write_bytes(contact_sheet(images, frames))
        self.images[name] = {
            "renderer": "studio.visuals.snapshot + Playwright + contact_sheet",
            "diagnostics": observations,
        }
        print(f"Captured {name}", flush=True)
        page.close()

    def run(self, deck: Deck) -> None:
        self.workspace()
        self.overview()
        self.projects()
        self.interactive()
        self.presenter()
        self.questions()
        # Restore the fictional canonical deck after the new-slide capture.
        self.app.store.write(DECK_ID, deck)
        self.open()
        self.references()
        self.visual_review(deck)
        if self.errors:
            raise RuntimeError(self.errors)
        expected = {
            str(path.relative_to(PROJECT))
            for folder in (PROJECT / "docs/assets", PROJECT / "docs/screenshots")
            for path in folder.glob("*.png")
        }
        if expected != self.images.keys():
            raise RuntimeError(f"Screenshot inventory mismatch: {expected ^ self.images.keys()}")
        for name, metadata in self.images.items():
            data = (self.output / name).read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError(f"Invalid PNG: {name}")
            metadata.update(
                pixels=list(struct.unpack(">II", data[16:24])),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        _ = (self.output / "docs/screenshots/captures.json").write_text(
            json.dumps(
                {"fixture": "Original fictional Studio Blue deck", "screenshots": self.images},
                indent=2,
            )
            + "\n"
        )
        self.context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=PROJECT, help="Root for docs/assets and docs/screenshots"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="slide-studio-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                Capture(app, browser, staging).run(seed(app))
            except Exception:
                print((root / "server.log").read_text()[-6000:])
                raise
            finally:
                browser.close()
        # Publish only after the complete capture flow and inventory checks pass.
        for source in staging.rglob("*"):
            if source.is_file():
                target = args.output.resolve() / source.relative_to(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(source, target)


if __name__ == "__main__":
    main()
