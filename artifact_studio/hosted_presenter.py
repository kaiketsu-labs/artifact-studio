"""Trusted hosted presentation controls around validated, isolated authored content."""

import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .models import AgentContext
from .multiplayer_access import Actor, workspace_editor_path
from .projects import AssetEmbedder
from .storage import SlideMarkup, Store
from .visuals import ScopedDeckEmbedder

_RUNTIME = Path(__file__).resolve().parent.parent / "runtime"


class _StaticMarkup(SlideMarkup):
    """Re-serialize validated static markup before placing it in a trusted DOM.

    Raw-text and namespace-switching containers belong in the opaque interactive
    document. Canonical attributes and escaped text avoid parser recovery turning
    malformed source or a comment into executable markup in the browser.
    """

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    @staticmethod
    def _tag(tag: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", tag) or tag in {
            "html",
            "head",
            "body",
            "math",
            "annotation-xml",
            "template",
            "noscript",
            "noembed",
            "noframes",
            "xmp",
            "plaintext",
        }:
            raise ValueError("Keep document containers in interactive_html.")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag(tag)
        super().handle_starttag(tag, attrs)
        if any(not re.fullmatch(r"[a-z_:][a-z0-9_.:-]*", name) for name, _ in attrs):
            raise ValueError("Static slide attributes must have valid names.")
        attributes = "".join(
            f" {name}" + ("" if value is None else f'="{escape(value, quote=True)}"')
            for name, value in attrs
        )
        self.parts.append(f"<{tag}{attributes}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.parts[-1] = self.parts[-1][:-1] + "/>"
        if tag == "style":
            self.in_style = False

    def handle_endtag(self, tag: str) -> None:
        self._tag(tag)
        super().handle_endtag(tag)
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        super().handle_data(data)
        self.parts.append(data.replace("<", r"\3c ") if self.in_style else escape(data))


def presenter_html(
    store: Store, deck_id: str, *, audience: bool = False, include_agent_context: bool = True
) -> str:
    """Keep the real deck identity and private notes only in the presenter response."""
    folder = store.folder(deck_id)
    if (store.root / deck_id).is_symlink() or any(
        (folder / name).is_symlink() or not (folder / name).is_file()
        for name in ("index.html", "theme.css")
    ):
        raise ValueError("Hosted presentations require regular, unlinked source files.")
    deck = store.read(deck_id).model_copy(deep=True)
    deck.id = deck_id
    embedder = (
        AssetEmbedder(folder) if include_agent_context else ScopedDeckEmbedder(store, deck_id)
    )
    theme = embedder.css(embedder.local_path("theme.css").read_text()).replace("<", r"\3c ")
    for slide in deck.slides:
        markup = _StaticMarkup()
        markup.feed(slide.html)
        markup.close()
        slide.html = embedder.markup("".join(markup.parts))
        if not include_agent_context and slide.interactive_html:
            slide.interactive_html = embedder.markup(slide.interactive_html)
        if audience:
            slide.notes = ""
            slide.notes_revision = 0
        if audience or not include_agent_context:
            slide.agent = AgentContext()
        for source in slide.sources:
            if not urlsplit(source.url).scheme:
                source.url = ""
    if audience:
        deck.sections = []
    # Only the canonical JSON is read from index.html. No authored shell, root
    # attributes, script files or HTML fragments become part of the host shell.
    shell = (_RUNTIME / "viewer.html").read_text()
    shell = shell.replace(
        '<link rel="stylesheet" href="theme.css">',
        "<style>"
        + theme
        + '</style><style id="slideshow-styles">'
        + (_RUNTIME / "slideshow.css").read_text()
        + "</style>",
    )
    shell = shell.replace("{{DECK_JSON}}", deck.model_dump_json().replace("<", r"\u003c"))
    for name in ("icons.js", "presentation.js"):
        shell = shell.replace(
            f'<script src="{name}"></script>', f'<script src="/runtime/{name}"></script>'
        )
    return shell


def hosted_presenter_router(
    get_store: Callable[[], Store],
    public_url: str,
    *,
    scoped_access: Callable[[Request, str], AbstractContextManager[Store]] | None = None,
) -> APIRouter:
    """Expose only the generated authenticated presenter, never stored index.html."""
    router = APIRouter()

    @router.get("/decks/{deck_id}/presenter", response_class=HTMLResponse)
    def presenter(
        deck_id: str, request: Request, audience: Literal["0", "1"] = "0"
    ) -> HTMLResponse:
        # URLSearchParams in the viewer reads the first value, while FastAPI
        # reads the last. Never let duplicate flags disagree about privacy.
        if len(request.query_params.getlist("audience")) > 1:
            raise HTTPException(status_code=400, detail="Ambiguous audience mode")
        try:
            access = scoped_access(request, deck_id) if scoped_access else nullcontext(get_store())
            with access as store:
                document = presenter_html(
                    store,
                    deck_id,
                    audience=audience == "1",
                    include_agent_context=scoped_access is None,
                )
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Presentation unavailable") from error
        workspace = getattr(request.state, "editor_workspace", "default")
        root = workspace_editor_path(str(workspace))
        actor = getattr(request.state, "actor", None)
        if scoped_access is not None and audience == "0" and isinstance(actor, Actor):
            document = document.replace(
                '<html lang="en">',
                f'<html lang="en" data-note-owner="{escape(actor.person, quote=True)}">',
                1,
            )
        return HTMLResponse(
            document,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; "
                    "script-src 'unsafe-inline' 'unsafe-eval' "
                    f"{public_url}/runtime/ {public_url}/web/; "
                    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
                    "media-src data: blob:; frame-src about:; "
                    f"connect-src {public_url}{root}api/decks/; "
                    "worker-src 'none'; object-src 'none'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'self'"
                ),
            },
        )

    return router
