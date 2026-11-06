"""Verify project version changes reach the service and native app bundle."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import install_macos


@pytest.mark.parametrize("release_tag", [None, "nightly-20261106.123"])
def test_version_from_project_reaches_runtime_and_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release_tag: str | None
) -> None:
    """A future release version is used without editing another version constant."""
    project = tmp_path / "checkout"
    package = project / "artifact_studio"
    package.mkdir(parents=True)
    _ = shutil.copy(Path(__file__).resolve().parents[1] / "artifact_studio/__init__.py", package)
    _ = (project / "pyproject.toml").write_text('[project]\nversion = "2.3.4"\n')
    script = project / "scripts/install_macos.py"
    script.parent.mkdir()
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project)
    result = subprocess.run(
        [sys.executable, "-c", "import artifact_studio; print(artifact_studio.__version__)"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "2.3.4"

    monkeypatch.setattr(install_macos, "__file__", str(script))
    monkeypatch.setattr(install_macos.platform, "system", lambda: "Darwin")

    def mock_run(*_args: str, env: dict[str, str] | None = None) -> str:
        """Skip native build tools while preserving bundle construction."""
        return ""

    monkeypatch.setattr(install_macos, "run", mock_run)

    def installed_release(_: Path) -> str | None:
        return release_tag

    monkeypatch.setattr(install_macos, "installed_release_tag", installed_release)
    destination = tmp_path / "Artifact Studio.app"
    install_macos.install(
        destination,
        collection=tmp_path / "collection",
        database=tmp_path / "review.sqlite3",
        port=8790,
        launch_at_login=False,
        qa=False,
    )
    with (destination / "Contents/Info.plist").open("rb") as plist_file:
        info = plistlib.load(plist_file)
    assert info["CFBundleShortVersionString"] == "2.3.4"
    assert info["CFBundleVersion"] == "2.3.4"
    assert info["ArtifactStudioReleaseTag"] == (release_tag or "")


def test_installer_direct_entrypoint_does_not_require_package_install(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = Path(__file__).resolve().parents[1] / "scripts/install_macos.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--destination" in result.stdout
