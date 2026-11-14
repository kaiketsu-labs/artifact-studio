"""Expose failing JUnit test IDs as bounded GitHub check annotations.

Only test identifiers and exception types are emitted, never traceback bodies,
request URLs or captured output. The full diagnostics remain in existing logs.
"""

import re
import sys
from pathlib import Path
from xml.etree import ElementTree


def escaped(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def report(paths: list[str]) -> None:
    remaining = 20
    for name in paths:
        path = Path(name)
        if not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        try:
            root = ElementTree.parse(path).getroot()
        except OSError, ElementTree.ParseError:
            continue
        for case in root.iter("testcase"):
            failed = case.find("failure")
            if failed is None:
                failed = case.find("error")
            if failed is None:
                continue
            if remaining == 0:
                print("::notice::Additional failed tests are listed in the JUnit artifact.")
                return
            identifier = "::".join(
                part for part in (case.get("classname", ""), case.get("name", "")) if part
            )[:1000]
            kind = failed.get("type", "Test failed")[:160]
            # Report source coordinates, not assertion values or captured diagnostics.
            frames = re.findall(r"(?m)^(tests/[A-Za-z0-9_/.-]+\.py):(\d+):", failed.text or "")
            location = f" at {frames[-1][0]}:{frames[-1][1]}" if frames else ""
            diagnostic = ""
            presenter_test = (
                "tests.integration.test_presentation_displays::"
                "test_presenter_click_opens_synchronized_audience_without_display_access"
            )
            if identifier.startswith(presenter_test):
                token = r"(?:r|rs|rn|rx|e|es|en|ex|c0|c1|q0|q1|k|h0|h1|a0|a1)"
                match = re.search(
                    rf"FULLSCREEN_TRACE=((?:{token},){{0,31}}{token})(?=\s|$)",
                    failed.text or "",
                )
                if match:
                    diagnostic = f" fullscreen events: {match.group(1)}"
            print(
                f"::error title=Failed test::{escaped(identifier)} "
                f"({escaped(kind)}){location}{diagnostic}"
            )
            remaining -= 1


if __name__ == "__main__":
    report(sys.argv[1:])
