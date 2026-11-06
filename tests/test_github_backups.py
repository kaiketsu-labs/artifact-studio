"""Scheduled private workspace archives with a fictional, in-memory GitHub remote."""

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Never, NotRequired, TypedDict, cast
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import pytest

from artifact_studio import github_backups, workspace_recovery
from artifact_studio.artifact_models import ArtifactFeedbackInput, ArtifactFileInput
from artifact_studio.artifacts import ArtifactStore
from artifact_studio.github_backups import FullBackupSettings, GitHubBackups
from artifact_studio.libraries import Libraries, Library
from artifact_studio.models import DesignRequestInput
from artifact_studio.storage import Store
from artifact_studio.workspace_recovery import ArchiveSummary, WorkspaceRecovery


class Asset(TypedDict):
    """Only GitHub's documented release-asset fields cross the fake CLI boundary."""

    id: int
    name: str
    size: int
    created_at: str
    browser_download_url: str
    state: str
    digest: NotRequired[str]


class Remote:
    """Emulate gh commands while exercising real ZIP export and checksum validation."""

    def __init__(self) -> None:
        self.assets: list[Asset] = []
        self.contents: dict[int, bytes] = {}
        self.calls: list[tuple[str, ...]] = []
        self.deleted: list[int] = []
        self.private = True
        self.fail_upload = False
        self.hide_upload = False
        self.wrong_upload_digest = False
        self.omit_upload_digest = False
        self.fail_delete = False
        self.corrupt_download = False
        self.release_body = github_backups.RELEASE_BODY
        self.release_exists = True
        self.tag_exists = False
        self.created_release: tuple[str, ...] | None = None
        self.created_tag: tuple[str, ...] | None = None
        self.upload_started = threading.Event()
        self.upload_continue = threading.Event()
        self.upload_continue.set()

    def add(
        self, name: str, content: bytes = b"old archive", *, created: str = "2025-01-01T00:00:00Z"
    ) -> Asset:
        """Make one release asset with a checksum independent of its filename."""
        identifier = max([0, *self.contents]) + 1
        asset: Asset = {
            "id": identifier,
            "name": name,
            "size": len(content),
            "created_at": created,
            "browser_download_url": f"https://github.com/fictional/backups/releases/download/{github_backups.RELEASE_TAG}/{name}",
            "state": "uploaded",
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        self.assets.append(asset)
        self.contents[identifier] = content
        return asset

    def __call__(
        self,
        *args: str,
        timeout: float = 180,
        output: Path | None = None,
        maximum_bytes: int | None = None,
    ) -> str:
        """Reject every unexpected network request instead of contacting GitHub."""
        _ = timeout
        self.calls.append(args)
        if args[:2] == ("repo", "view"):
            return json.dumps(
                {
                    "isPrivate": self.private,
                    "defaultBranchRef": {"name": "main", "target": {"oid": "a" * 40}},
                }
            )
        if args[:2] == ("release", "upload"):
            self.upload_started.set()
            if not self.upload_continue.wait(timeout=10):
                raise ValueError("Fictional upload did not finish")
            if self.fail_upload:
                raise ValueError("Fictional upload failed")
            if not self.hide_upload:
                path = Path(args[3])
                asset = self.add(path.name, path.read_bytes(), created="2026-11-05T12:00:00Z")
                if self.wrong_upload_digest:
                    asset["digest"] = "sha256:" + "0" * 64
                if self.omit_upload_digest:
                    _ = asset.pop("digest", None)
            return ""
        if args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            asset = next(item for item in self.assets if item["name"] == name)
            destination = Path(args[args.index("--output") + 1])
            content = self.contents[asset["id"]]
            if self.corrupt_download:
                content = b"x" * len(content)
            _ = destination.write_bytes(content)
            return ""
        if args[0] == "api":
            endpoint = next((item for item in args if item.startswith("repos/")), "")
            parsed = urlsplit(endpoint)
            endpoint = parsed.path
            if "/git/ref/tags/" in endpoint:
                if not self.tag_exists:
                    raise github_backups.GitHubCommandError("HTTP 404", not_found=True)
                return json.dumps({"object": {"type": "commit", "sha": "b" * 40}})
            if endpoint.endswith("/git/tags") and "POST" in args:
                self.created_tag = args
                return json.dumps({"sha": "c" * 40})
            if endpoint.endswith("/git/refs") and "POST" in args:
                self.tag_exists = True
                return json.dumps({"ref": f"refs/tags/{github_backups.RELEASE_TAG}"})
            if "/releases/assets/" in endpoint and output is not None:
                identifier = int(endpoint.rsplit("/", maxsplit=1)[1])
                content = self.contents[identifier]
                if self.corrupt_download:
                    content = b"x" * len(content)
                if maximum_bytes is not None and len(content) > maximum_bytes:
                    raise ValueError("Fictional download exceeds its size limit")
                _ = output.write_bytes(content)
                return ""
            if "/commits/" in endpoint:
                return json.dumps({"sha": "a" * 40})
            if "/releases/tags/" in endpoint:
                if not self.release_exists:
                    raise github_backups.GitHubCommandError("HTTP 404", not_found=True)
                return json.dumps(
                    {
                        "id": 101,
                        "tag_name": github_backups.RELEASE_TAG,
                        "body": self.release_body,
                        "draft": False,
                        "prerelease": True,
                    }
                )
            if endpoint.endswith("/releases") and "POST" in args:
                self.release_exists = True
                self.created_release = args
                self.release_body = next(
                    item.removeprefix("body=") for item in args if item.startswith("body=")
                )
                return json.dumps(
                    {
                        "id": 101,
                        "tag_name": github_backups.RELEASE_TAG,
                        "body": self.release_body,
                        "draft": False,
                        "prerelease": True,
                    }
                )
            if endpoint.endswith("/releases/101/assets"):
                page = int(parse_qs(parsed.query).get("page", ["1"])[0])
                assets = self.assets[(page - 1) * 100 : page * 100]
                return json.dumps([assets] if "--slurp" in args else assets)
            if "/releases/assets/" in endpoint and "DELETE" in args:
                if self.fail_delete:
                    raise ValueError("Fictional deletion failed")
                identifier = int(endpoint.rsplit("/", maxsplit=1)[1])
                self.deleted.append(identifier)
                self.assets = [item for item in self.assets if item["id"] != identifier]
                return ""
        raise AssertionError(f"Unexpected gh command: {args}")


def workspace(path: Path) -> Store:
    """Keep fictional collection files separate from the private review database."""
    return Store(path / "collection", path / "state" / "review.sqlite3")


def connected(store: Store) -> Library:
    """Register an existing private repository without cloning or granting source sync."""
    _ = Libraries(store)
    library = Library(
        id="fictional-github",
        kind="github",
        location="fictional/backups",
        path=str(store.database.parent / "libraries" / "fictional-github"),
    )
    with store.connect() as db:
        _ = db.execute(
            "INSERT INTO libraries VALUES (?, ?)", (library.id, library.model_dump_json())
        )
    return library


def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[GitHubBackups, Library, Remote]:
    """Route every gh operation to a fake, while retaining real storage and archive code."""
    store = workspace(tmp_path)
    library = connected(store)
    service = GitHubBackups(store)
    remote = Remote()
    monkeypatch.setattr(github_backups, "gh", remote)
    monkeypatch.setattr(
        github_backups, "time", SimpleNamespace(time=lambda: 1_000, monotonic=time.monotonic)
    )
    return service, library, remote


def archive_name(series: str, *, stamp: str = "20250101T000000Z", token: str = "1" * 32) -> str:
    """Use the exact managed series namespace rather than a permissive filename prefix."""
    checksum = hashlib.sha256(b"old archive").hexdigest()
    return f"workspace-{series}-{stamp}-{token}-{checksum}.artifact-studio.zip"


def forget_verified_fingerprint(service: GitHubBackups, identifier: str) -> None:
    """Represent a checkpoint saved by an installation predating content fingerprints."""
    with service._connect() as db:  # pyright: ignore[reportPrivateUsage]
        row = db.execute(
            "SELECT document FROM github_backup_state WHERE id=?", (identifier,)
        ).fetchone()
        assert row is not None
        document = cast(dict[str, object], json.loads(str(row[0])))
        _ = document.pop("verified_fingerprint")
        _ = db.execute(
            "UPDATE github_backup_state SET document=? WHERE id=?",
            (json.dumps(document), identifier),
        )


def test_backup_permission_is_separate_and_defaults_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connected repository never grants permission to upload private workspace state."""
    service, library, remote = setup(tmp_path, monkeypatch)
    state = service.statuses()[0]
    assert state.library_id == library.id
    assert state.phase == "disabled" and not state.settings.enabled
    assert state.settings.interval_hours == 24 and state.settings.keep_count == 7
    with pytest.raises(ValueError, match="[Ee]nable"):
        _ = service.queue(library.id)
    service.tick(now=1_000)
    assert not remote.calls


def test_failed_or_unverified_upload_never_prunes_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old recovery points survive both a failed transfer and a missing upload acknowledgement."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    remote.fail_upload = True
    service.tick(now=1_000)
    assert service.state(library.id).phase == "error"
    assert not remote.deleted and old in remote.assets
    remote.fail_upload = False
    remote.hide_upload = True
    _ = service.queue(library.id)
    service.tick(now=2_000)
    assert service.state(library.id).phase == "error"
    assert not remote.deleted and old in remote.assets


def test_retention_only_removes_own_series_after_verified_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retention leaves other workspaces and unrelated release assets alone."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    newer = remote.add(
        archive_name(service.workspace_id, stamp="20250201T000000Z", token="2" * 32),
        created="2025-02-01T00:00:00Z",
    )
    other = remote.add(archive_name("f" * 32))
    unrelated = remote.add("release-package.zip")
    lookalike = remote.add(f"workspace-{service.workspace_id}-notes.zip")
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=2))
    service.tick(now=1_000)
    state = service.state(library.id)
    assert state.phase == "backed_up" and not state.error
    assert remote.deleted == [old["id"]]
    assert all(item in remote.assets for item in (newer, other, unrelated, lookalike))
    assert state.archive_name in [item["name"] for item in remote.assets]


def test_remote_checksum_disagreement_prevents_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub's upload acknowledgement cannot override a conflicting content digest."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    remote.wrong_upload_digest = True
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    assert service.state(library.id).phase == "error"
    assert not remote.deleted and old in remote.assets


def test_retention_failure_is_warning_after_successful_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deletion problem must not hide a completed, recoverable new archive."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = remote.add(archive_name(service.workspace_id))
    remote.fail_delete = True
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    state = service.state(library.id)
    assert state.phase == "backed_up" and state.last_backup_at
    assert state.warning and not state.error
    assert len(remote.assets) == 2


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_github_digest_requires_download_verification_before_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    """Older GitHub metadata without a digest still needs verified uploaded bytes."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    remote.omit_upload_digest = True
    remote.corrupt_download = corrupt
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    state = service.state(library.id)
    if corrupt:
        assert state.phase == "error" and old in remote.assets and not remote.deleted
    else:
        assert state.phase == "backed_up" and remote.deleted == [old["id"]]
    assert any("Accept: application/octet-stream" in call for call in remote.calls)
    if not corrupt:
        download_index = next(
            index
            for index, call in enumerate(remote.calls)
            if "Accept: application/octet-stream" in call
        )
        deletion_index = next(index for index, call in enumerate(remote.calls) if "DELETE" in call)
        assert download_index < deletion_index


def test_asset_discovery_reads_later_pages_before_verifying_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrelated assets filling the first page cannot hide the new backup asset."""
    service, library, remote = setup(tmp_path, monkeypatch)
    for number in range(101):
        _ = remote.add(f"unrelated-{number}.zip")
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    assert service.state(library.id).phase == "backed_up"
    assert len(remote.assets) == 102 and not remote.deleted
    assert any(any("page=2" in argument for argument in call) for call in remote.calls)


def test_schedule_survives_restart_and_disabled_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart preserves due time and disabled workspaces never catch up unexpectedly."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, interval_hours=2))
    service.tick(now=1_000)
    first = service.state(library.id)
    assert first.phase == "backed_up" and first.next_backup_at
    due = datetime.fromisoformat(first.next_backup_at).timestamp()
    restarted = GitHubBackups(service.store)
    assert restarted.workspace_id == service.workspace_id
    restarted.tick(now=due - 1)
    assert len(remote.assets) == 1
    restarted.tick(now=due + 1)
    unchanged = restarted.state(library.id)
    assert unchanged.phase == "unchanged"
    assert unchanged.last_backup_at == first.last_backup_at
    assert unchanged.archive_name == first.archive_name and unchanged.url == first.url
    assert len(remote.assets) == 1
    restarted.tick(now=due + 2)
    assert len(remote.assets) == 1
    _ = restarted.configure(library.id, FullBackupSettings(enabled=False))
    GitHubBackups(service.store).tick(now=due + 1_000_000)
    assert len(remote.assets) == 1
    assert restarted.state(library.id).phase == "disabled"


def test_unchanged_scheduled_and_manual_checks_skip_zip_network_and_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated checks keep the confirmed recovery point without creating another ZIP."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, interval_hours=2))
    service.tick(now=1_000)
    first = service.state(library.id)
    assert first.next_backup_at is not None
    due = datetime.fromisoformat(first.next_backup_at).timestamp()
    remote.calls.clear()

    def forbid_zip(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("An unchanged workspace must not create a ZIP")

    with monkeypatch.context() as unchanged_patch:
        unchanged_patch.setattr(workspace_recovery, "ZipFile", forbid_zip)
        restarted = GitHubBackups(service.store)
        restarted.tick(now=due)
        assert restarted.state(library.id).phase == "unchanged"
        assert restarted.state(library.id).next_backup_at == github_backups._iso(  # pyright: ignore[reportPrivateUsage]
            due + 2 * 3600
        )
        _ = restarted.queue(library.id)
        restarted.tick(now=due + 1)
        _ = restarted.configure(
            library.id, FullBackupSettings(enabled=True, interval_hours=3, keep_count=1)
        )
        _ = restarted.queue(library.id)
        restarted.tick(now=due + 2)
        state = restarted.state(library.id)
        assert state.phase == "unchanged" and not state.error
        assert state.last_backup_at == first.last_backup_at
        assert state.archive_name == first.archive_name and state.url == first.url
        assert state.next_backup_at == github_backups._iso(due + 2 + 3 * 3600)  # pyright: ignore[reportPrivateUsage]
        assert not remote.calls and not remote.deleted
        assert len(remote.assets) == 1

    _ = service.store.post_design_request(
        DesignRequestInput(message="A saved discussion changed", request_id="later-change")
    )
    _ = service.queue(library.id)
    service.tick(now=due + 3)
    assert service.state(library.id).phase == "backed_up"
    assert service.state(library.id).archive_name != first.archive_name
    assert len(remote.assets) == 1 and len(remote.deleted) == 1


def test_unchanged_check_clears_error_but_preserves_retention_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping a duplicate does not claim that an earlier cleanup warning was resolved."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = remote.add(archive_name(service.workspace_id))
    remote.fail_delete = True
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    first = service.state(library.id)
    assert first.warning and first.next_backup_at is not None

    def previous_error(record: github_backups._Checkpoint) -> None:  # pyright: ignore[reportPrivateUsage]
        record.status.error = "An earlier local check failed"
        record.status.phase = "error"
        record.due_at = 2_000

    _ = service._change(library.id, previous_error)  # pyright: ignore[reportPrivateUsage]
    remote.calls.clear()
    remote.fail_delete = False
    GitHubBackups(service.store).tick(now=2_000)
    state = service.state(library.id)
    assert state.phase == "unchanged" and not state.error
    assert state.warning == first.warning
    assert state.last_backup_at == first.last_backup_at
    assert not remote.calls and not remote.deleted


def test_failed_changed_upload_does_not_checkpoint_its_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed changed snapshot is retried until its remote checksum is confirmed."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    _ = service.store.post_design_request(
        DesignRequestInput(message="Must be backed up", request_id="retry-change")
    )
    _ = service.queue(library.id)
    remote.wrong_upload_digest = True
    service.tick(now=2_000)
    assert service.state(library.id).phase == "error"
    assert len(remote.assets) == 2
    remote.wrong_upload_digest = False
    # Remove the deliberately invalid acknowledgement; it is not a usable recovery point.
    remote.assets.pop()
    _ = service.queue(library.id)
    GitHubBackups(service.store).tick(now=3_000)
    assert service.state(library.id).phase == "backed_up"
    assert len(remote.assets) == 2
    remote.calls.clear()
    _ = service.queue(library.id)
    service.tick(now=4_000)
    assert service.state(library.id).phase == "unchanged" and not remote.calls


def test_change_during_upload_is_saved_by_the_next_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verified checkpoint describes the exported snapshot, not later workspace edits."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    remote.upload_continue.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        exporting = executor.submit(service.tick, 1_000)
        assert remote.upload_started.wait(timeout=5)
        try:
            _ = service.store.post_design_request(
                DesignRequestInput(message="Edited during upload", request_id="during-upload")
            )
        finally:
            remote.upload_continue.set()
        exporting.result(timeout=5)
    first = service.state(library.id)
    assert first.phase == "backed_up"
    _ = service.queue(library.id)
    GitHubBackups(service.store).tick(now=2_000)
    assert service.state(library.id).phase == "backed_up"
    assert len(remote.assets) == 2
    remote.calls.clear()
    _ = service.queue(library.id)
    service.tick(now=3_000)
    assert service.state(library.id).phase == "unchanged" and not remote.calls


def test_older_checkpoint_verifies_its_archive_and_skips_duplicate_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upgrade derives the checkpoint from the existing archive instead of duplicating it."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    first = service.state(library.id)
    forget_verified_fingerprint(service, library.id)
    original_zip = workspace_recovery.ZipFile

    def read_only_zip(
        source: Path,
        mode: Literal["r", "w", "x", "a"] = "r",
        compression: int = 0,
        *,
        compresslevel: int | None = None,
    ) -> ZipFile:
        assert mode == "r", "Migration must not create a duplicate ZIP archive"
        return original_zip(source, mode, compression, compresslevel=compresslevel)

    monkeypatch.setattr(workspace_recovery, "ZipFile", read_only_zip)
    remote.calls.clear()
    restarted = GitHubBackups(service.store)
    _ = restarted.queue(library.id)
    restarted.tick(now=2_000)
    state = restarted.state(library.id)
    assert state.phase == "unchanged" and len(remote.assets) == 1
    assert state.last_backup_at == first.last_backup_at
    assert state.archive_name == first.archive_name and state.url == first.url
    assert any("Accept: application/octet-stream" in call for call in remote.calls)
    assert not any(call[:2] == ("release", "upload") for call in remote.calls)
    assert not remote.deleted
    remote.calls.clear()
    again = GitHubBackups(service.store)
    _ = again.queue(library.id)
    again.tick(now=3_000)
    assert again.state(library.id).phase == "unchanged" and not remote.calls


def test_older_checkpoint_uploads_changed_content_after_verifying_previous_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration preserves the distinction between the prior archive and current saved edits."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    first = service.state(library.id)
    forget_verified_fingerprint(service, library.id)
    _ = service.store.post_design_request(
        DesignRequestInput(message="Changed before upgrading", request_id="upgrade-change")
    )
    remote.calls.clear()
    restarted = GitHubBackups(service.store)
    _ = restarted.queue(library.id)
    restarted.tick(now=2_000)
    state = restarted.state(library.id)
    assert state.phase == "backed_up" and state.archive_name != first.archive_name
    assert len(remote.assets) == 1 and len(remote.deleted) == 1
    download_index = next(
        index
        for index, call in enumerate(remote.calls)
        if "Accept: application/octet-stream" in call
    )
    upload_index = next(
        index for index, call in enumerate(remote.calls) if call[:2] == ("release", "upload")
    )
    assert download_index < upload_index
    remote.calls.clear()
    _ = restarted.queue(library.id)
    restarted.tick(now=3_000)
    assert restarted.state(library.id).phase == "unchanged" and not remote.calls


def test_older_checkpoint_missing_archive_creates_a_verified_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remotely removed recovery point is replaced without requiring a checkpoint reset."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    first = service.state(library.id)
    forget_verified_fingerprint(service, library.id)
    remote.assets.clear()
    remote.fail_upload = True
    _ = service.queue(library.id)
    service.tick(now=2_000)
    assert service.state(library.id).phase == "error"
    assert not service._record(library.id).verified_fingerprint  # pyright: ignore[reportPrivateUsage]
    assert not remote.deleted
    remote.fail_upload = False
    _ = service.queue(library.id)
    GitHubBackups(service.store).tick(now=3_000)
    state = service.state(library.id)
    assert state.phase == "backed_up" and state.archive_name != first.archive_name
    assert len(remote.assets) == 1 and not remote.deleted
    remote.calls.clear()
    _ = service.queue(library.id)
    GitHubBackups(service.store).tick(now=4_000)
    assert service.state(library.id).phase == "unchanged" and not remote.calls


@pytest.mark.parametrize("failure", ["corrupt", "invalid_archive"])
def test_older_checkpoint_requires_a_valid_previous_archive_before_seeding_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """An untrusted old recovery point cannot suppress uploads or permit pruning."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    first = service.state(library.id)
    forget_verified_fingerprint(service, library.id)
    if failure == "corrupt":
        remote.corrupt_download = True
    else:
        invalid = b"This is not a workspace archive."
        digest = hashlib.sha256(invalid).hexdigest()
        name = (
            first.archive_name[: -len(".artifact-studio.zip") - 64]
            + digest
            + ".artifact-studio.zip"
        )
        asset = remote.assets[0]
        asset["name"] = name
        asset["size"] = len(invalid)
        asset["digest"] = "sha256:" + digest
        remote.contents[asset["id"]] = invalid

        def invalid_receipt(record: github_backups._Checkpoint) -> None:  # pyright: ignore[reportPrivateUsage]
            record.status.archive_name = name

        _ = service._change(library.id, invalid_receipt)  # pyright: ignore[reportPrivateUsage]
    remote.calls.clear()
    restarted = GitHubBackups(service.store)
    _ = restarted.queue(library.id)
    restarted.tick(now=2_000)
    state = restarted.state(library.id)
    assert state.phase == "error" and state.error and state.next_backup_at is not None
    assert datetime.fromisoformat(state.next_backup_at).timestamp() > 2_000
    assert state.last_backup_at == first.last_backup_at
    assert not restarted._record(library.id).verified_fingerprint  # pyright: ignore[reportPrivateUsage]
    assert not any(call[:2] == ("release", "upload") for call in remote.calls)
    assert not remote.deleted


def test_failed_schedule_retries_after_persistent_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Temporary failures remain visible and retry once due without a browser or manual queue."""
    service, library, remote = setup(tmp_path, monkeypatch)
    remote.private = False
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    state = service.state(library.id)
    assert state.phase == "error" and state.next_backup_at
    due = datetime.fromisoformat(state.next_backup_at).timestamp()
    assert due > 1_000
    attempted_calls = len(remote.calls)
    restarted = GitHubBackups(service.store)
    restarted.tick(now=due - 1)
    assert len(remote.calls) == attempted_calls
    remote.private = True
    restarted.tick(now=due)
    assert restarted.state(library.id).phase == "backed_up"
    assert len(remote.assets) == 1


def test_private_repository_checked_before_every_upload_and_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository visibility change stops subsequent private-state transfers."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    assert len(remote.assets) == 1
    remote.private = False
    _ = service.store.post_design_request(
        DesignRequestInput(message="Changed private discussion", request_id="private-change")
    )
    _ = service.queue(library.id)
    service.tick(now=2_000)
    assert service.state(library.id).phase == "error"
    assert len(remote.assets) == 1 and not remote.deleted
    with pytest.raises(ValueError, match="private"):
        _ = service.download(library.id, remote.assets[0]["id"], tmp_path / "forbidden.zip")
    assert not (tmp_path / "forbidden.zip").exists()


def test_new_backup_release_is_pinned_to_default_branch_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating a backup container never tags the local application checkout by accident."""
    service, library, remote = setup(tmp_path, monkeypatch)
    remote.release_exists = False
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    assert service.state(library.id).phase == "backed_up"
    assert remote.created_release is not None
    assert "target_commitish=" + "a" * 40 in remote.created_release
    assert "make_latest=false" in remote.created_release
    assert "prerelease=true" in remote.created_release
    assert remote.created_tag is not None
    assert "object=" + "a" * 40 in remote.created_tag
    assert "type=commit" in remote.created_tag


def test_existing_unowned_backup_tag_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reserved tag name alone does not authorize adopting an existing repository tag."""
    service, library, remote = setup(tmp_path, monkeypatch)
    remote.release_exists = False
    remote.tag_exists = True
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    assert service.state(library.id).phase == "error"
    assert remote.created_release is None and remote.created_tag is None
    assert not remote.assets and not remote.deleted


def test_download_rejects_checksum_mismatch_and_unowned_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed filename cannot authorize corrupt bytes or an unrelated release file."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    remote.corrupt_download = True
    destination = tmp_path / "corrupt.zip"
    with pytest.raises(ValueError, match="checksum|SHA|digest"):
        _ = service.download(library.id, remote.assets[0]["id"], destination)
    assert not destination.exists()
    unrelated = remote.add("unrelated.zip")
    with pytest.raises(ValueError):
        _ = service.download(library.id, unrelated["id"], tmp_path / "unrelated.zip")
    remote.release_body = "An actual application release"
    with pytest.raises(ValueError):
        _ = service.archives(library.id)


def test_another_pc_can_discover_all_managed_workspace_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery discovery includes other machines without granting their deletion rights."""
    service, library, _remote = setup(tmp_path / "origin", monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    second_store = workspace(tmp_path / "second-pc")
    second_library = connected(second_store)
    second = GitHubBackups(second_store)
    assert second.workspace_id != service.workspace_id
    listed = second.archives(second_library.id)
    assert len(listed) == 1 and listed[0].workspace_id == service.workspace_id
    recovered = second.download(
        second_library.id, listed[0].asset_id, tmp_path / "from-first-pc.zip"
    )
    assert recovered.is_file()
    assert not second.state(second_library.id).settings.enabled


def test_uploaded_zip_restores_approval_chat_and_immutable_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GitHub route round-trips the full archive rather than only current source files."""
    service, library, remote = setup(tmp_path / "origin", monkeypatch)
    registry = ArtifactStore(service.store)
    project = registry.create_project("Fictional project", "Fictional context")
    source = tmp_path / "authored"
    source.mkdir()
    _ = (source / "index.html").write_text("<h1>First</h1>")
    artifact = registry.register_artifact(project.id, "Fictional artifact", source, "index.html")
    artifact = registry.revise_artifact(
        artifact.id,
        [ArtifactFileInput(path="index.html", text="<h1>Second</h1>")],
        artifact.content_hash,
        "fictional-revision",
    )
    _ = registry.approve(artifact.id, artifact.content_hash, True)
    _ = registry.add_feedback(
        artifact.id,
        ArtifactFeedbackInput(
            message="Private review",
            request_id="fictional-feedback",
            content_hash=artifact.content_hash,
        ),
    )
    _ = service.store.post_design_request(
        DesignRequestInput(message="Private design chat", request_id="fictional-chat")
    )
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    service.tick(now=1_000)
    assert service.state(library.id).phase == "backed_up"
    archive = service.download(library.id, remote.assets[0]["id"], tmp_path / "restore.zip")
    target = workspace(tmp_path / "restored")
    recovery = WorkspaceRecovery(target)
    preview = recovery.preview(archive, mode="archive")
    assert preview.can_restore
    _ = recovery.restore(archive, preview.content_hash, mode="archive")
    restored = ArtifactStore(target)
    assert restored.get_artifact(artifact.id).approved
    assert restored.snapshot_files(artifact.id, 1)["index.html"] == b"<h1>First</h1>"
    assert restored.snapshot_files(artifact.id, 2)["index.html"] == b"<h1>Second</h1>"
    assert restored.feedback(artifact.id, include_pending=True)[0].message == "Private review"
    assert target.design_inbox().messages[0].message == "Private design chat"
    restored_backups = GitHubBackups(target)
    assert not restored_backups.statuses()
    assert restored_backups.workspace_id != service.workspace_id


def test_cross_process_lock_prevents_overlapping_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second service instance cannot upload the same queued workspace concurrently."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    remote.upload_continue.clear()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.tick, 1_000)
        assert remote.upload_started.wait(timeout=5)
        try:
            second = executor.submit(GitHubBackups(service.store).tick, 1_000)
            second.result(timeout=2)
        finally:
            remote.upload_continue.set()
        first.result(timeout=5)
    assert len(remote.assets) == 1


def test_worker_cancellation_waits_for_inflight_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown keeps an in-flight upload tracked until its status has been committed."""
    service, library, remote = setup(tmp_path, monkeypatch)
    _ = service.configure(library.id, FullBackupSettings(enabled=True))
    remote.upload_continue.clear()

    async def scenario() -> None:
        stop = asyncio.Event()
        worker = asyncio.create_task(service.run(stop))
        try:
            assert await asyncio.to_thread(remote.upload_started.wait, 5)
            _ = worker.cancel()
            await asyncio.sleep(0)
            assert not worker.done()
        finally:
            remote.upload_continue.set()
            stop.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=5)

    try:
        asyncio.run(scenario())
    finally:
        remote.upload_continue.set()
    assert service.state(library.id).phase == "backed_up"
    assert len(remote.assets) == 1


def test_disabling_during_export_prevents_upload_and_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling backups takes effect before a completed local ZIP can leave this machine."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    original_export = WorkspaceRecovery.export_archive_if_changed

    def export_and_disable(
        recovery: WorkspaceRecovery, destination: Path, previous_fingerprint: str
    ) -> ArchiveSummary:
        summary = original_export(recovery, destination, previous_fingerprint)
        assert destination.is_file()
        _ = service.configure(library.id, FullBackupSettings(enabled=False))
        return summary

    monkeypatch.setattr(WorkspaceRecovery, "export_archive_if_changed", export_and_disable)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    service.tick(now=1_000)
    state = service.state(library.id)
    assert state.phase == "disabled" and state.last_backup_at is None
    assert remote.assets == [old] and not remote.deleted
    assert not remote.upload_started.is_set()


def test_disable_from_new_service_completes_while_workspace_export_is_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An active ZIP export cannot block revoking permission from another request."""
    service, library, remote = setup(tmp_path, monkeypatch)
    old = remote.add(archive_name(service.workspace_id))
    export_started = threading.Event()
    export_continue = threading.Event()
    original_copy = workspace_recovery._copy_verified  # pyright: ignore[reportPrivateUsage]

    def pause_locked_copy(
        source: Path, destination: Path, expected: workspace_recovery.RecoveryFile
    ) -> None:
        if destination.parent.name == "objects" and not export_started.is_set():
            export_started.set()
            if not export_continue.wait(timeout=5):
                raise ValueError("Fictional export was not released")
        original_copy(source, destination, expected)

    def disable_from_new_service() -> None:
        fresh = GitHubBackups(service.store)
        state = fresh.configure(library.id, FullBackupSettings(enabled=False))
        assert state.phase == "disabled" and not state.settings.enabled

    monkeypatch.setattr(workspace_recovery, "_copy_verified", pause_locked_copy)
    _ = service.configure(library.id, FullBackupSettings(enabled=True, keep_count=1))
    with ThreadPoolExecutor(max_workers=2) as executor:
        exporting = executor.submit(service.tick, 1_000)
        assert export_started.wait(timeout=5)
        try:
            disabling = executor.submit(disable_from_new_service)
            disabling.result(timeout=2)
            assert not export_continue.is_set()
        finally:
            export_continue.set()
        exporting.result(timeout=5)
    state = service.state(library.id)
    assert state.phase == "disabled" and state.last_backup_at is None
    assert remote.assets == [old] and not remote.deleted
    assert not remote.upload_started.is_set()


def fake_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Run the actual bounded subprocess helper against a local fictional gh program."""
    binary = tmp_path / "bin" / "gh"
    binary.parent.mkdir()
    _ = binary.write_text(f"#!{sys.executable}\n" + body + "\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ.get("PATH", ""))


def test_gh_helper_streams_binary_response_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binary release bytes pass through the real CLI helper without text decoding."""
    content = bytes(range(256)) * 256
    fake_executable(
        tmp_path, monkeypatch, "import sys\nsys.stdout.buffer.write(bytes(range(256)) * 256)"
    )
    destination = tmp_path / "download.zip"
    result = github_backups.gh("api", "fictional", output=destination, maximum_bytes=len(content))
    assert result == "" and destination.read_bytes() == content


def test_gh_helper_enforces_size_limit_and_removes_partial_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote response exceeding its announced size cannot leave a restore candidate."""
    fake_executable(tmp_path, monkeypatch, "import sys\nsys.stdout.buffer.write(b'x' * 65536)")
    destination = tmp_path / "oversized.zip"
    with pytest.raises(ValueError, match="size"):
        _ = github_backups.gh("api", "fictional", output=destination, maximum_bytes=128)
    assert not destination.exists()


def test_gh_helper_timeout_cleans_up_partial_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled CLI is stopped within its transfer timeout and its partial file is removed."""
    fake_executable(
        tmp_path,
        monkeypatch,
        "import sys, time\nsys.stdout.buffer.write(b'partial')\nsys.stdout.flush()\ntime.sleep(30)",
    )
    destination = tmp_path / "stalled.zip"
    started = time.monotonic()
    with pytest.raises(ValueError, match="timed out"):
        _ = github_backups.gh("api", "fictional", output=destination, timeout=0.1)
    assert time.monotonic() - started < 3
    assert not destination.exists()


def test_gh_helper_bounds_failure_diagnostics_and_preserves_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded CLI errors retain useful status while an existing download is never replaced."""
    fake_executable(
        tmp_path,
        monkeypatch,
        "import sys\nsys.stderr.write('x' * 65536 + 'HTTP 404 final diagnostic')\nsys.exit(1)",
    )
    with pytest.raises(github_backups.GitHubCommandError) as failure:
        _ = github_backups.gh("api", "fictional")
    assert failure.value.not_found
    assert "final diagnostic" in str(failure.value)
    assert len(str(failure.value)) < 1400
    existing = tmp_path / "existing.zip"
    _ = existing.write_bytes(b"existing recovery point")
    with pytest.raises(FileExistsError):
        _ = github_backups.gh("api", "fictional", output=existing)
    assert existing.read_bytes() == b"existing recovery point"
