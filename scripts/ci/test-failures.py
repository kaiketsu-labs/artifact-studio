"""Expose failing JUnit test IDs as bounded GitHub check annotations.

Only test identifiers and exception types are emitted, never traceback bodies,
request URLs or captured output. The full diagnostics remain in existing logs.
"""

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
        except (OSError, ElementTree.ParseError):
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
            print(f"::error title=Failed test::{escaped(identifier)} ({escaped(kind)})")
            remaining -= 1


if __name__ == "__main__":
    report(sys.argv[1:])
