"""Validated deployment settings; hosted mode never falls back to local mode."""

import json
import os
import re
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
            return cls()
        if mode != "hosted":
            raise ValueError("Unknown deployment mode")
        filename = os.environ.get("ARTIFACT_STUDIO_HOSTED_CONFIG")
        if not filename:
            raise ValueError("Hosted configuration is required")
        try:
            data: object = json.loads(Path(filename).read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("Invalid hosted configuration") from exc
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
        provider = data["auth_provider"]
        options = data["auth_options"]
        if (
            not isinstance(provider, str)
            or not provider
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
    if not isinstance(value, str):
        raise ValueError("Invalid HTTPS origin")
    parts = urlsplit(value)
    host = parts.hostname
    if (
        parts.scheme != "https"
        or not host
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.port not in (None, 443)
        or not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host
        )
    ):
        raise ValueError("Invalid HTTPS DNS origin")
    return f"https://{host}"


def _strings(value: object, *, email: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Invalid membership list")
    value = cast(list[object], value)
    if not all(isinstance(item, str) and item and item.strip() == item for item in value):
        raise ValueError("Invalid membership list")
    strings = cast(list[str], value)
    result = tuple(item.lower() if email else item for item in strings)
    if len(set(result)) != len(result) or (
        email and any(not re.fullmatch(r"[^@\s]+@[^@\s]+", item) for item in result)
    ):
        raise ValueError("Invalid membership list")
    return result
