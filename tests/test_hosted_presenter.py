"""Generated hosted presenters keep authored code and private audience data separate."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artifact_studio.hosted_presenter import hosted_presenter_router, presenter_html
from artifact_studio.models import AgentContext
from artifact_studio.storage import DATA, Store
from tests.design_factory import TEMPLATE_ID, make_design


def fixture_store(root: Path) -> Store:
    make_design(root / TEMPLATE_ID)
    return Store(root, root / "state/review.sqlite3")


def test_presenter_uses_trusted_shell_and_strips_audience_private_data(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    deck = store.read(TEMPLATE_ID)
    deck.id = "incorrect-source-identity"
    deck.title = "Fictional </script><script>window.injection=true</script>"
    deck.slides[0].notes = "PRIVATE_NOTE </script><script>window.noteInjection=true</script>"
    deck.slides[0].agent = AgentContext(summary="PRIVATE_AGENT", questions=["PRIVATE_QUESTION"])
    store.write(TEMPLATE_ID, deck)
    source = store.folder(TEMPLATE_ID) / "index.html"
    source.write_text(source.read_text() + "<script>window.authoredShell=true</script>")
    (store.folder(TEMPLATE_ID) / "icons.js").write_text("window.authoredRuntime=true")
    theme = store.folder(TEMPLATE_ID) / "theme.css"
    theme.write_text(
        theme.read_text() + "/* </style><script>window.themeInjection=true</script> */"
    )
    presenter = presenter_html(store, TEMPLATE_ID)
    assert '"id":"' + TEMPLATE_ID + '"' in presenter
    assert "PRIVATE_NOTE" in presenter
    assert "PRIVATE_AGENT" in presenter
    assert "authoredShell" not in presenter
    assert "authoredRuntime" not in presenter
    assert "</style><script>window.themeInjection" not in presenter
    assert "</script><script>window.noteInjection" not in presenter
    assert "</script><script>window.injection" not in presenter
    assert '<script src="/runtime/presentation.js"></script>' in presenter
    assert "data:image/svg+xml;base64," in presenter
    audience = presenter_html(store, TEMPLATE_ID, audience=True)
    assert "PRIVATE_" not in audience
    assert '"notes":""' in audience
    assert DATA.search(audience) is not None


@pytest.mark.parametrize(
    "html",
    [
        '<article class="slide"><img src="assets/custom.svg" onload="alert(1)"></article>',
        '<article class="slide"><script>alert(1)</script></article>',
        '<article class="slide"><noscript><p title="</noscript>'
        '<img src=x onerror=alert(1)>"></p></noscript></article>',
        '<article class="slide"><math><mtext><table><mglyph><style><!--</style>'
        '<img title="--><img src=x onerror=alert(1)>"></table></mtext></math></article>',
    ],
)
def test_presenter_rejects_active_and_parser_switching_static_markup(
    tmp_path: Path, html: str
) -> None:
    store = fixture_store(tmp_path)
    deck = store.read(TEMPLATE_ID)
    # Exercise startup-era authored files as well as validated remote writes.
    deck.slides[0].html = html
    store.write(TEMPLATE_ID, deck)
    with pytest.raises(ValueError):
        presenter_html(store, TEMPLATE_ID)


def test_presenter_response_policy_and_unlinked_source_boundary(tmp_path: Path) -> None:
    store = fixture_store(tmp_path)
    app = FastAPI()
    app.include_router(hosted_presenter_router(lambda: store, "https://studio.example.test"))
    with TestClient(app) as client:
        response = client.get(f"/decks/{TEMPLATE_ID}/presenter")
        assert response.status_code == 200
        policy = response.headers["content-security-policy"]
        assert "frame-src about:" in policy
        assert "connect-src https://studio.example.test/api/decks/" in policy
        assert "img-src data: blob:" in policy
        assert "base-uri 'none'" in policy
        assert client.get(f"/decks/{TEMPLATE_ID}/presenter?audience=true").status_code == 422
        assert (
            client.get(f"/decks/{TEMPLATE_ID}/presenter?audience=1&audience=0").status_code == 400
        )
        assert client.get("/decks/missing/presenter").status_code == 404
        theme = store.folder(TEMPLATE_ID) / "theme.css"
        replacement = tmp_path / "other.css"
        replacement.write_text("body{}")
        theme.unlink()
        theme.symlink_to(replacement)
        assert client.get(f"/decks/{TEMPLATE_ID}/presenter").status_code == 404
