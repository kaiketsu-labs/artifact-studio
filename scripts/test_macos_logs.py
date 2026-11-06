"""Compile and run the Foundation-only diagnostic log checks with Swift 6."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    """Exercise real file rotation and subprocess pipes on macOS or Linux."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--swiftc", default=shutil.which("swiftc"))
    args = parser.parse_args()
    if not args.swiftc:
        parser.error("Install a Swift 6 compiler, or pass --swiftc /path/to/swiftc.")
    project = Path(__file__).resolve().parent.parent
    sources = [
        project / "macos" / name for name in ("DiagnosticLogs.swift", "DiagnosticLogCapture.swift")
    ]
    with tempfile.TemporaryDirectory(prefix="artifact-studio-log-tests-") as directory:
        temporary = Path(directory)
        for name in ("DiagnosticLogsTests", "DiagnosticLogCaptureTests"):
            executable = temporary / name
            subprocess.run(
                [
                    args.swiftc,
                    "-swift-version",
                    "6",
                    "-warnings-as-errors",
                    "-parse-as-library",
                    "-module-cache-path",
                    str(temporary / "modules"),
                    *map(str, sources),
                    str(project / "tests" / "native" / f"{name}.swift"),
                    "-o",
                    str(executable),
                ],
                check=True,
                timeout=120,
            )
            subprocess.run([str(executable)], check=True, timeout=45)


if __name__ == "__main__":
    main()
