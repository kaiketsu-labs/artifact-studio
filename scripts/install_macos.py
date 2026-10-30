"""Build a native menu-bar controller for an existing local Slide Studio installation."""

import argparse
import json
import os
import platform
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(*arguments: str, env: dict[str, str] | None = None) -> str:
    """Run a build tool without a shell and retain useful failure diagnostics."""
    result = subprocess.run(arguments, text=True, capture_output=True, env=env, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def install(
    destination: Path,
    *,
    collection: Path,
    database: Path,
    port: int,
    launch_at_login: bool,
    qa: bool,
) -> None:
    """Compile, validate and install an app without moving the user's workspace or database."""
    if platform.system() != "Darwin":
        raise ValueError("The menu-bar app requires macOS 13 or later.")
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a port between 1024 and 65535.")
    project = Path(__file__).resolve().parent.parent
    python = project / ".venv/bin/python"
    if not python.exists():
        raise ValueError("Set up Slide Studio first with uv sync --locked --python 3.14.")
    if destination.suffix != ".app" or destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new .app destination; quit and move an older app aside first.")
    environment = os.environ.copy()
    command_line_tools = Path("/Library/Developer/CommandLineTools")
    if command_line_tools.exists() and "DEVELOPER_DIR" not in environment:
        environment["DEVELOPER_DIR"] = str(command_line_tools)
    cache = project / ".state/macos-build-cache"
    cache.mkdir(parents=True, exist_ok=True)
    environment["CLANG_MODULE_CACHE_PATH"] = str(cache / "clang")
    environment["SWIFT_MODULECACHE_PATH"] = str(cache / "swift")
    compiler = run("/usr/bin/xcrun", "--find", "swiftc", env=environment)
    sdk = run("/usr/bin/xcrun", "--show-sdk-path", env=environment)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="slide-studio-build-") as staging:
        name = "Slide Studio QA" if qa else "Slide Studio"
        bundle_id = "labs.kaiketsu.slide-studio" + (".qa" if qa else "")
        bundle = Path(staging) / destination.name
        contents = bundle / "Contents"
        resources, binaries = contents / "Resources", contents / "MacOS"
        resources.mkdir(parents=True)
        binaries.mkdir()
        binary = binaries / "SlideStudio"
        _ = run(
            compiler,
            "-swift-version",
            "6",
            "-parse-as-library",
            "-O",
            "-target",
            f"{platform.machine()}-apple-macos13.0",
            "-sdk",
            sdk,
            str(project / "macos/SlideStudio.swift"),
            "-framework",
            "AppKit",
            "-framework",
            "ServiceManagement",
            "-o",
            str(binary),
            env=environment,
        )
        iconset = Path(staging) / "SlideStudio.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = str(size * scale)
                suffix = "@2x" if scale == 2 else ""
                _ = run(
                    "/usr/bin/sips",
                    "-z",
                    pixels,
                    pixels,
                    str(project / "web/brand/mark.png"),
                    "--out",
                    str(iconset / f"icon_{size}x{size}{suffix}.png"),
                )
        _ = run(
            "/usr/bin/iconutil",
            "-c",
            "icns",
            str(iconset),
            "-o",
            str(resources / "SlideStudio.icns"),
        )
        (contents / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": bundle_id,
                    "CFBundleName": name,
                    "CFBundleDisplayName": name,
                    "CFBundleExecutable": "SlideStudio",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "0.1.0",
                    "CFBundleVersion": "1",
                    "CFBundleIconFile": "SlideStudio.icns",
                    "LSMinimumSystemVersion": "13.0",
                    "LSUIElement": True,
                    "NSHighResolutionCapable": True,
                }
            )
        )
        _ = (resources / "configuration.json").write_text(
            json.dumps(
                {
                    "project": str(project),
                    "python": str(python),
                    "collection": str(collection.expanduser().resolve()),
                    "database": str(database.expanduser().resolve()),
                    "port": port,
                    "launchAtLogin": launch_at_login,
                },
                indent=2,
            )
            + "\n"
        )
        for path in (bundle, *bundle.rglob("*")):
            for attribute in {"com.apple.FinderInfo", "com.apple.ResourceFork"}.intersection(
                run("/usr/bin/xattr", str(path)).splitlines()
            ):
                _ = run("/usr/bin/xattr", "-d", attribute, str(path))
        _ = run(
            "/usr/bin/codesign", "--force", "--sign", "-", "--identifier", bundle_id, str(bundle)
        )
        _ = run("/usr/bin/codesign", "--verify", "--strict", str(bundle))
        _ = shutil.move(str(bundle), str(destination))
        try:
            _ = run("/usr/bin/codesign", "--verify", "--strict", str(destination))
        except RuntimeError:
            _ = shutil.move(str(destination), str(bundle))
            raise


def main() -> None:
    """Read explicit local-install settings and print the installed app location."""
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=Path.home() / "Applications/Slide Studio.app"
    )
    parser.add_argument("--collection", type=Path, default=Path.home() / "Documents/Presentations")
    parser.add_argument("--database", type=Path, default=project / ".state/review.sqlite3")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--launch-at-login", action="store_true")
    parser.add_argument(
        "--qa", action="store_true", help="Use a separate app identity for disposable testing."
    )
    args = parser.parse_args()
    install(
        args.destination.expanduser().absolute(),
        collection=args.collection,
        database=args.database,
        port=args.port,
        launch_at_login=args.launch_at_login,
        qa=args.qa,
    )
    print(f"Installed {args.destination}. Open it in Finder to start Slide Studio.")


if __name__ == "__main__":
    main()
