"""Hosted routing must fail closed and keep authored files off the app origin."""

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from artifact_studio.hosted_policy import authorize, scoped_deck_routes
from artifact_studio.storage import Store


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/api/decks/demo/events"),
        ("POST", "/api/decks/demo/slides/one/feedback"),
        ("POST", "/api/decks/demo/slides/one/image"),
        ("POST", "/api/projects/open"),
        ("POST", "/api/artifacts/import"),
        ("POST", "/mcp"),
        ("POST", "/api/review/hooks"),
        ("GET", "/api/agent/hooks-client"),
        ("POST", "/api/artifacts/demo/review/disconnect"),
        ("GET", "/api/session"),
        ("POST", "/api/shares"),
        ("GET", "/api/shares/token/file"),
        ("GET", "/decks/demo/theme.css"),
        ("GET", "/decks/demo/presenter"),
        ("GET", "/decks/demo/assets/image.svg"),
        ("GET", "/web/hub.js"),
    ],
)
def test_allowed_routes(method: str, path: str) -> None:
    assert authorize(method, path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/mcp"),
        ("GET", "/api/libraries"),
        ("POST", "/api/app-info/settings"),
        ("GET", "/api/workspace/archive"),
        ("GET", "/api/copilot/setup/guide"),
        ("GET", "/decks/demo/index.html"),
        ("GET", "/decks/demo/.state/private"),
        ("GET", "/decks/demo/assets/../index.html"),
        ("GET", "/decks/demo/assets/%2e%2e/index.html"),
        ("POST", "/api/decks/demo/new-admin"),
        ("OPTIONS", "/api/decks"),
        ("DELETE", "/api/decks/demo"),
    ],
)
def test_denied_routes(method: str, path: str) -> None:
    assert not authorize(method, path)


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, deck_id: str) -> None:
        if deck_id != "demo":
            raise ValueError("Unknown deck")

    def folder(self, deck_id: str) -> Path:
        return self.root / deck_id


def test_deck_assets(tmp_path: Path) -> None:
    folder = tmp_path / "demo"
    assets = folder / "assets"
    assets.mkdir(parents=True)
    (folder / "theme.css").write_text("body {}")
    (folder / "index.html").write_text("<script>bad()</script>")
    (assets / "image.svg").write_text("<svg/>")
    (assets / "bad.html").write_text("<script>bad()</script>")
    (assets / "link.svg").symlink_to(assets / "image.svg")
    router = scoped_deck_routes(lambda: cast(Store, FakeStore(tmp_path)))
    endpoint = cast(Callable[[str, str], FileResponse], cast(APIRoute, router.routes[0]).endpoint)
    assert endpoint("demo", "theme.css").status_code == 200
    svg = endpoint("demo", "assets/image.svg")
    assert "sandbox" in svg.headers["content-security-policy"]
    for path in ("index.html", "assets/bad.html", "assets/link.svg", "assets/.hidden.svg"):
        with pytest.raises(HTTPException) as error:
            endpoint("demo", path)
        assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        endpoint("unknown", "theme.css")


def test_registered_routes_are_reviewed() -> None:
    """Every literal registered route must be allowed or deliberately local-only."""
    import ast
    import re

    source_root = Path(__file__).parents[1] / "artifact_studio"
    modules = (
        "app",
        "artifact_api",
        "chat_references",
        "share_api",
        "history_api",
        "element_alternative_api",
        "slide_design_api",
        "image_api",
        "recovery_api",
        "favorites",
        "hosted_presenter",
    )
    # Local management, host integrations, and the unscoped local MCP are excluded.
    denied = {
        ("GET", "/api/copilot/setup/{document}"),
        ("POST", "/api/app-info/settings"),
        ("GET", "/api/libraries"),
        ("POST", "/api/libraries"),
        ("POST", "/api/libraries/{library_id}/settings"),
        ("GET", "/api/library-sync"),
        ("GET", "/api/workspace/github-backups"),
        ("POST", "/api/libraries/{library_id}/full-backup/settings"),
        ("POST", "/api/libraries/{library_id}/full-backup/run"),
        ("GET", "/api/libraries/{library_id}/full-backup/archives"),
        ("POST", "/api/libraries/{library_id}/retry"),
        ("POST", "/api/libraries/{library_id}/refresh"),
        ("POST", "/api/libraries/{library_id}/sync"),
        ("GET", "/api/libraries/{library_id}/files/{path:path}"),
        ("GET", "/api/workspace/archive"),
        ("POST", "/api/workspace/recovery/library"),
        ("POST", "/api/workspace/recovery/github-archive"),
        ("POST", "/api/workspace/recovery/upload"),
        ("POST", "/api/workspace/recovery/{token}/restore"),
        ("DELETE", "/api/workspace/recovery/{token}"),
        ("POST", "/api/shares/{token}/outlook"),
    }
    unreviewed: list[str] = []
    for module in modules:
        tree = ast.parse((source_root / f"{module}.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in {"get", "post", "delete", "patch", "put"}
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                ):
                    continue
                method, route = decorator.func.attr.upper(), decorator.args[0].value
                if module == "share_api":
                    route = "/api/shares" + route
                sample = re.sub(r"\{[^}]+\}", "example", route)
                if not authorize(method, sample) and (method, route) not in denied:
                    unreviewed.append(f"{method} {route}")
    assert not unreviewed, unreviewed
