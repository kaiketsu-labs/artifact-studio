"""Tests for the local Outlook handoff without launching Outlook."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from artifact_studio import share_outlook


def test_draft_stages_attachment_and_passes_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve attachment bytes and invoke Outlook with a safe filename."""
    source = tmp_path / 'a "file".pdf'
    source.write_bytes(b"pdf data")
    app = tmp_path / "Outlook.app"
    app.mkdir()
    runner = Mock()
    monkeypatch.setattr(share_outlook.sys, "platform", "darwin")
    monkeypatch.setattr(share_outlook, "_OUTLOOK", app)

    def make_stage(*, prefix: str) -> str:
        assert prefix == "artifact-studio-outlook-"
        return str(tmp_path / "stage")

    monkeypatch.setattr(share_outlook.tempfile, "mkdtemp", make_stage)
    (tmp_path / "stage").mkdir()
    monkeypatch.setattr(share_outlook.subprocess, "run", runner)
    share_outlook.create_outlook_draft(source)

    args = runner.call_args.args[0]
    assert args[:3] == ["/usr/bin/open", "-b", "com.microsoft.Outlook"]
    assert Path(args[3]).read_bytes() == b"pdf data"
    assert Path(args[3]).name == "a _file_.pdf"


def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reject unsupported platforms before invoking Outlook."""
    monkeypatch.setattr(share_outlook.sys, "platform", "linux")
    with pytest.raises(ValueError, match="only available on macOS"):
        share_outlook.create_outlook_draft(tmp_path / "x")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.CalledProcessError(1, "open", stderr="Launch failed"), "Launch failed"),
        (subprocess.TimeoutExpired("open", 60), "may already have been created"),
    ],
)
def test_outlook_errors_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, message: str
) -> None:
    """Surface failed and uncertain handoffs instead of reporting success."""
    app = tmp_path / "Outlook.app"
    app.mkdir()
    source = tmp_path / "file.pdf"
    source.write_bytes(b"data")
    monkeypatch.setattr(share_outlook.sys, "platform", "darwin")
    monkeypatch.setattr(share_outlook, "_OUTLOOK", app)
    monkeypatch.setattr(share_outlook.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(ValueError, match=message):
        share_outlook.create_outlook_draft(source)
