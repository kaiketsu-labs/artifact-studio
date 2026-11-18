"""Edit imported chart values through the real slide editor."""

import os
import re

import pytest
from playwright.sync_api import Page, expect

from artifact_studio.artifacts import ArtifactStore
from tests.integration.support import RunningApp
from tests.pptx_fixture import fictional_powerpoint

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_edit_chart_value_and_reload(live_app: RunningApp, page: Page) -> None:
    project = ArtifactStore(live_app.store).create_project("Fictional chart")
    page.goto(f"{live_app.url}/?project={project.id}")
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import artifact")
    dialog.get_by_label("Title", exact=True).fill("Garden chart")
    page.locator("#hub-import-files").set_input_files(
        {
            "name": "fictional-garden.pptx",
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "buffer": fictional_powerpoint(),
        }
    )
    dialog.get_by_role("button", name="Import presentation").click()
    expect(page.frame_locator("#preview").get_by_text("Harbor Garden")).to_be_visible(timeout=30000)
    page.locator("#slides [data-id]").nth(1).click()
    frame = page.frame_locator("#preview")

    def assert_chart_geometry(expected_ratio: float) -> None:
        chart = frame.locator("[data-pptx-chart]")
        geometry = chart.evaluate("""el => {
          const zero = el.querySelector('[data-chart-tick="0"]').getBoundingClientRect();
          const maximum = el.querySelector('[data-chart-tick="1"]').getBoundingClientRect();
          const bar = el.querySelectorAll('[data-chart-bar]')[1].getBoundingClientRect();
          return {zero: zero.top, maximum: maximum.top, top: bar.top, bottom: bar.bottom};
        }""")
        assert abs(geometry["zero"] - geometry["bottom"]) < 2
        assert (
            abs(
                (geometry["zero"] - geometry["top"]) / (geometry["zero"] - geometry["maximum"])
                - expected_ratio
            )
            < 0.02
        )

    assert_chart_geometry(0.8)
    value = frame.locator('[data-chart-value="12"]')
    value.click()
    panel = page.locator("#element-properties")
    panel.get_by_role("button", name="Edit text", exact=True).click()
    panel.get_by_role("textbox", name="Text", exact=True).fill("60")
    panel.get_by_role("button", name="Save text change", exact=True).click()
    expect(frame.locator('[data-chart-value="60"]')).to_be_visible()
    expect(frame.locator("[data-pptx-chart]")).to_have_attribute("data-chart-max", "60")
    expect(frame.locator("[data-chart-bar]").first).to_have_attribute(
        "style", re.compile("height:70.0000%")
    )
    expect(frame.locator('[data-chart-tick="0.5"]')).to_have_text("30")
    assert_chart_geometry(0.4)
    page.reload()
    page.locator("#slides [data-id]").nth(1).click()
    value = page.frame_locator("#preview").locator('[data-chart-value="60"]')
    expect(value).to_be_visible()
    value.click()
    panel.get_by_role("button", name="Edit text", exact=True).click()
    panel.get_by_role("textbox", name="Text", exact=True).fill("0")
    panel.get_by_role("button", name="Save text change", exact=True).click()
    # An empty bar still has a clickable value at the plot baseline.
    frame.locator('[data-chart-value="0"]').click()
    panel.get_by_role("button", name="Edit text", exact=True).click()
    expect(panel.get_by_role("textbox", name="Text", exact=True)).to_have_value("0")
