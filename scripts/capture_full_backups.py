"""Capture full-backup controls through actual UI actions and fictional GitHub responses.

Run: uv run python -m scripts.capture_full_backups [--axe /path/to/axe.min.js]
No repository connection, GitHub upload, user workspace or production renderer is used.
"""

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright

from artifact_studio.agent_client import object_value
from artifact_studio.libraries import Libraries, Library
from scripts.screenshot_fixture import seed
from tests.integration.support import DECK_ID, PROJECT, running_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT)
    parser.add_argument("--axe", type=Path, help="Optional local axe-core script")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="full-backup-docs-") as directory:
        root = Path(directory)
        staging = root / "captures"
        captures: dict[str, object] = {}
        audits: dict[str, object] = {}
        with running_app(root, root / "server.log") as app, sync_playwright() as playwright:
            _ = seed(app)
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1200},
                color_scheme="light",
                reduced_motion="reduce",
                locale="en-GB",
                timezone_id="UTC",
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "response",
                lambda response: (
                    errors.append(f"HTTP {response.status}: {response.url}")
                    if response.status >= 400
                    else None
                ),
            )
            library_id = "fictional-full-backup"
            collection = Libraries(app.store).list()[0].model_dump()
            repository = Library(
                id=library_id,
                kind="github",
                location="fictional-studio/private-workspace",
                path="/tmp/fictional-backup-reference-cache",
            ).model_dump()
            state: dict[str, object] = {
                "library_id": library_id,
                "settings": {"enabled": False, "interval_hours": 24, "keep_count": 7},
                "phase": "disabled",
                "last_backup_at": "",
                "next_backup_at": "",
                "error": "",
                "warning": "",
                "archive_name": "",
                "url": "",
            }
            writes: list[dict[str, object]] = []

            def settings(route: Route) -> None:
                body = object_value(route.request.post_data_json)
                writes.append(body)
                state.update(
                    settings=body,
                    phase="backed_up",
                    last_backup_at="2026-11-05T09:30:00Z",
                    next_backup_at="2026-11-06T09:30:00Z",
                )
                route.fulfill(json=state)

            page.route(
                "**/api/libraries", lambda route: route.fulfill(json=[collection, repository])
            )
            page.route("**/api/library-sync", lambda route: route.fulfill(json=[]))
            page.route("**/api/workspace/github-backups", lambda route: route.fulfill(json=[state]))
            page.route(f"**/api/libraries/{library_id}/full-backup/settings", settings)
            assets = [
                {
                    "asset_id": 301,
                    "name": "fictional-other-pc.artifact-studio.zip",
                    "created_at": "2026-11-05T09:30:00Z",
                    "size": 4_500_000,
                    "workspace_id": "fictional-other-pc",
                    "url": "https://github.com/fictional-studio/private-workspace/releases/301",
                }
            ]
            page.route(
                f"**/api/libraries/{library_id}/full-backup/archives",
                lambda route: route.fulfill(json=assets),
            )

            def capture(name: str) -> None:
                expect(page.locator("#toast")).to_be_hidden(timeout=10_000)
                _ = page.evaluate("document.fonts.ready")
                _ = page.evaluate(
                    "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
                page.mouse.move(1, 1)
                target = staging / "docs/screenshots" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                image = page.screenshot(path=target, animations="disabled")
                captures["docs/screenshots/" + name] = {
                    "viewport": page.viewport_size,
                    "pixels": list(page.viewport_size.values()) if page.viewport_size else [],
                    "captured_at": datetime.now(UTC).isoformat(),
                    "sha256": hashlib.sha256(image).hexdigest(),
                }
                if args.axe:
                    page.add_script_tag(path=str(args.axe))
                    audits[name] = page.evaluate("async()=> (await axe.run()).violations")
                print("Captured " + name, flush=True)

            def appearance(value: str) -> None:
                page.locator("#close-libraries").click()
                page.get_by_role("button", name="Menu", exact=True).click()
                page.get_by_role("menuitem", name="Appearance", exact=True).click()
                page.get_by_role("menuitemradio", name=value, exact=True).click()
                expect(page.locator("html")).to_have_attribute("data-theme", value.lower())
                page.locator("#storage-status").click()

            try:
                page.goto(f"{app.url}/?deck={DECK_ID}#workflow")
                page.locator("#storage-status").click()
                dialog = page.get_by_role("dialog", name="Storage and backup")
                full = dialog.locator(f'details[data-full-backup="{library_id}"]')
                full.locator("summary").click()
                full.get_by_role("checkbox", name="Back up full workspace", exact=True).check()
                full.get_by_role("button", name="Save backup settings", exact=True).click()
                expect(full).to_contain_text("Full workspace backed up")
                expect(full.get_by_role("spinbutton", name="Every (hours)")).to_have_value("24")
                expect(full.get_by_role("spinbutton", name="Keep latest backups")).to_have_value(
                    "7"
                )
                full.scroll_into_view_if_needed()
                capture("full-github-backup.png")
                appearance("Dark")
                full.scroll_into_view_if_needed()
                capture("full-github-backup-dark.png")
                page.set_viewport_size({"width": 390, "height": 844})
                full.scroll_into_view_if_needed()
                assert full.evaluate("node => node.scrollWidth <= node.clientWidth")
                assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
                capture("full-github-backup-mobile.png")
                page.set_viewport_size({"width": 1440, "height": 1200})
                appearance("Light")
                dialog.get_by_role("button", name="Restore workspace", exact=True).click()
                recovery = page.locator("dialog.workspace-recovery")
                recovery.get_by_role("combobox", name="Recovery source").select_option(
                    label="Full GitHub backup"
                )
                recovery.get_by_role("button", name="Load backups", exact=True).click()
                recovery.get_by_role("combobox", name="Workspace backup").select_option("301")
                expect(recovery.get_by_role("button", name="Preview full backup")).to_be_enabled()
                capture("full-github-backup-restore.png")
                assert writes == [{"enabled": True, "interval_hours": 24, "keep_count": 7}]
                if errors:
                    raise RuntimeError("\n".join(errors))
                if args.axe and any(audits.values()):
                    report = Path("/tmp/full-backup-axe.json")
                    _ = report.write_text(json.dumps(audits, indent=2) + "\n")
                    raise RuntimeError("Axe violations: " + str(report))
                manifest = staging / "docs/screenshots/full-backup-captures.json"
                _ = manifest.write_text(
                    json.dumps(
                        {
                            "fixture": "Original fictional Studio Blue presentation and repository",
                            "captured_at": datetime.now(UTC).isoformat(),
                            "remote_boundary": "GitHub API responses use fictional fixtures; "
                            "no repository is connected and no private data is uploaded.",
                            "verification": "Actual UI opens, expands, opts in, saves, changes "
                            "appearance, loads remote backup fixtures, and selects another PC. "
                            "Screenshots are direct browser pixels with no DOM replacement.",
                            "screenshots": captures,
                            "axe": audits if args.axe else "Not requested",
                        },
                        indent=2,
                    )
                    + "\n"
                )
            finally:
                context.close()
                browser.close()
        for source in staging.rglob("*"):
            if source.is_file():
                target = args.output.resolve() / source.relative_to(staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = shutil.copy2(source, target)


if __name__ == "__main__":
    main()
