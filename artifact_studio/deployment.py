"""Validated deployment settings; hosted mode never falls back to local mode."""

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DeploymentConfig:
    mode: Literal["local", "hosted"] = "local"
    public_url: str = ""
    preview_url: str = ""
    auth_provider: str = ""
    auth_options: dict[str, object] | None = None
    member_emails: tuple[str, ...] = ()
    agent_subjects: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> DeploymentConfig:
        mode = os.environ.get("ARTIFACT_STUDIO_MODE", "local")
        if mode == "local":
            if os.environ.get("ARTIFACT_STUDIO_HOSTED_CONFIG"):
                raise ValueError(
                    "Hosted configuration is set but mode is local; set hosted mode "
                    "or unset the configuration path."
                )
            return cls()
        if mode != "hosted":
            raise ValueError("Unknown deployment mode; use local or hosted.")
        filename = os.environ.get("ARTIFACT_STUDIO_HOSTED_CONFIG")
        if not filename:
            raise ValueError(
                "Set ARTIFACT_STUDIO_HOSTED_CONFIG to the private hosted configuration file."
            )
        try:
            config_path = Path(filename)
            collection = Path(
                os.environ.get(
                    "ARTIFACT_STUDIO_COLLECTION", str(Path.home() / "Documents/Artifacts")
                )
            ).resolve()
            if config_path.resolve().is_relative_to(collection):
                raise ValueError("Keep the hosted configuration outside the served collection.")
            fd = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as config_file:
                info = os.fstat(config_file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(
                        "Hosted configuration must be a regular file, "
                        "not a symlink or special file."
                    )
                if info.st_mode & 0o022:
                    raise ValueError(
                        "Hosted configuration must not be group- or world-writable; "
                        "change its permissions (for example, chmod 640)."
                    )
                raw = config_file.read(65537)
            if len(raw) > 65536:
                raise ValueError("Hosted configuration exceeds 64 KiB; reduce its size.")
            data: object = json.loads(raw, object_pairs_hook=_unique_keys)
        except OSError as exc:
            raise ValueError(
                "Cannot read hosted configuration; use a readable regular file, not a symlink."
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Hosted configuration must contain valid UTF-8 JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid hosted configuration fields")
        data = cast(dict[str, object], data)
        if set(data) != {
            "public_url",
            "preview_url",
            "auth_provider",
            "auth_options",
            "member_emails",
            "agent_subjects",
        }:
            raise ValueError("Invalid hosted configuration fields")
        public = _origin(data["public_url"])
        preview = _origin(data["preview_url"])
        if public == preview or urlsplit(public).hostname == urlsplit(preview).hostname:
            raise ValueError("Workspace and preview hosts must differ")
        if cast(str, urlsplit(public).hostname).endswith(
            "." + cast(str, urlsplit(preview).hostname)
        ):
            raise ValueError(
                "Preview host must not be a parent domain of the workspace host; "
                "choose a separate preview host."
            )
        provider = data["auth_provider"]
        options = data["auth_options"]
        if (
            not isinstance(provider, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", provider)
            or not isinstance(options, dict)
            or not all(isinstance(k, str) for k in cast(dict[object, object], options))
        ):
            raise ValueError("Invalid authentication provider")
        options = cast(dict[str, object], options)
        members = _strings(data["member_emails"], email=True)
        agents = _strings(data["agent_subjects"])
        if not members and not agents:
            raise ValueError("Hosted membership is required")
        return cls("hosted", public, preview, provider, options, members, agents)


def _origin(value: object) -> str:
    if not isinstance(value, str) or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in value):
        raise ValueError("Invalid HTTPS origin")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError("Invalid HTTPS DNS origin") from None
    if (
        parts.scheme != "https"
        or not host
        or host == "localhost.localdomain"
        or host.endswith(".localhost")
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or port not in (None, 443)
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host
        )
    ):
        raise ValueError("Invalid HTTPS DNS origin")
    return f"https://{host}"


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(
                "Hosted configuration contains duplicate JSON keys; remove duplicates."
            )
        result[key] = value
    return result


def _strings(value: object, *, email: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Invalid membership list")
    value = cast(list[object], value)
    if not all(
        isinstance(item, str)
        and 0 < len(item) <= 512
        and item.strip() == item
        and not any(ord(c) < 32 or ord(c) == 127 for c in item)
        for item in value
    ):
        raise ValueError("Invalid membership list")
    strings = cast(list[str], value)
    result = tuple(item.lower() if email else item for item in strings)
    if len(set(result)) != len(result) or (
        email and any(not re.fullmatch(r"[^@\s]+@[^@\s]+", item) for item in result)
    ):
        raise ValueError("Invalid membership list")
    return result
