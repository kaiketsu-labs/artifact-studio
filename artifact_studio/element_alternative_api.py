"""Human-only element decisions and sandboxed audience-only proposal previews."""

import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .element_alternatives import ElementAlternatives, ElementPage
from .multiplayer_access import Role
from .slide_designs import DecideDesign, DesignDecision, UndoDesign
from .storage import Store


def _focus_element(document: str, target_id: str) -> str:
    """Focus the frozen target using trusted presentation code, never author scripts."""
    match = re.search(r'<script nonce="([a-f0-9]+)"', document)
    if match is None:
        raise ValueError("The frozen element preview is missing its trusted runtime.")
    target = json.dumps(target_id).replace("<", "\\u003c")
    script = """(async()=>{await window.ArtifactStudioCaptureReady;
      await document.fonts.ready;requestAnimationFrame(()=>{
      const node=document.getElementById(TARGET);if(!node)return;
      const box=node.getBoundingClientRect();if(!box.width||!box.height)return;
      const backdrop=getComputedStyle(node.closest('.slide')||document.body).background;
      for(const item of document.querySelectorAll('body *'))
        item.style.setProperty('visibility','hidden','important');
      for(const item of [node,...node.querySelectorAll('*')])
        item.style.setProperty('visibility','visible','important');
      document.documentElement.style.background=backdrop;
      document.body.style.background='transparent';
      const scale=Math.min(1440/box.width,740/box.height);
      const x=(1600-box.width*scale)/2-box.x*scale,y=(900-box.height*scale)/2-box.y*scale;
      document.body.style.transformOrigin='0 0';
      document.body.style.transform=`translate(${x}px,${y}px) scale(${scale})`;
      document.documentElement.dataset.elementPreviewReady='true';
    });})();""".replace("TARGET", target)
    before, closing = document.rsplit("</body>", 1)
    return before + f'<script nonce="{match[1]}">{script}</script></body>' + closing


def element_alternative_routes(
    access: Callable[[Request, str, Role], AbstractContextManager[Store]],
    *,
    scoped: bool = False,
) -> APIRouter:
    router = APIRouter()
    base = "/api/decks/{deck_id}/slides/{slide_id}/element-alternatives"

    @router.get(base)
    def alternatives(
        request: Request,
        deck_id: str,
        slide_id: str,
        cursor: int | None = None,
        decision_cursor: int | None = None,
        set_id: str | None = None,
    ) -> ElementPage:
        with access(request, deck_id, "view") as store:
            return ElementAlternatives(store, scoped=scoped).list(
                deck_id, slide_id, cursor=cursor, decision_cursor=decision_cursor, set_id=set_id
            )

    @router.get(base + "/{option_id}/preview")
    def preview(
        request: Request,
        deck_id: str,
        slide_id: str,
        option_id: str,
        context_hash: str | None = None,
        element: bool = False,
    ) -> HTMLResponse:
        with access(request, deck_id, "view") as store:
            service = ElementAlternatives(store, scoped=scoped)
            source = service.read(deck_id, slide_id, option_id, context_hash)
            frozen = service.preview(deck_id, slide_id, option_id, source.context_hash)
        document = _focus_element(frozen.document, source.target_id) if element else frozen.document
        return HTMLResponse(
            document,
            headers={
                "Cache-Control": "no-store" if scoped else "private, max-age=31536000, immutable",
                "Content-Security-Policy": "sandbox allow-scripts; frame-ancestors 'self'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post(base + "/decide")
    def decide(request: Request, deck_id: str, slide_id: str, body: DecideDesign) -> DesignDecision:
        with access(request, deck_id, "edit") as store:
            return ElementAlternatives(store, scoped=scoped).decide(deck_id, slide_id, body)

    @router.post(base + "/undo")
    def undo(request: Request, deck_id: str, slide_id: str, body: UndoDesign) -> DesignDecision:
        with access(request, deck_id, "edit") as store:
            return ElementAlternatives(store, scoped=scoped).undo(deck_id, slide_id, body)

    return router
