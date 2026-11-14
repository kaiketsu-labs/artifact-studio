"""Open a fictional saved presentation in a disconnected browser."""

import functools
import http.server
import os
import threading
from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import Route, sync_playwright

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("ARTIFACT_STUDIO_BROWSER_TESTS") != "1", reason="Opt-in browser journeys"
    ),
]


def test_saved_shell_opens_and_queues_while_offline() -> None:
    root = Path(__file__).resolve().parents[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            )
            context = browser.new_context()
            page = context.new_page()
            origin = f"http://127.0.0.1:{server.server_port}"
            page.route(
                "**/offline-snapshot",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    json={
                        "cache_scope": "a" * 64,
                        "receipts": {"cover": "fictional-receipt"},
                        "theme": "h1 { color: rgb(12, 34, 56); }",
                        "assets": {
                            "assets/test.svg": "data:image/svg+xml;base64,"
                            "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmci"
                            "IHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCI+PC9zdmc+"
                        },
                    },
                ),
            )
            page.goto(origin + "/web/offline.html")
            page.evaluate("""async () => {
              const scope='a'.repeat(64);
              await OfflineClient.bind(scope);
              await OfflineClient.save(Promise.resolve({cache_scope:scope}),
                {id:'deck',title:'Fictional',slides:[
                  {id:'cover',title:'Cover',html:'<h1>Original</h1><img src="assets/test.svg">',
                   content_hash:'hash-original',expected_hash:'source-original'}
                ]},'/api/session','default');
            }""")
            page.route(
                "**/api/session",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"' + "a" * 64 + '"}',
                ),
            )
            pending: list[Route] = []
            page.route("**/api/decks/deck", lambda route: pending.append(route))
            page.reload()
            page.wait_for_timeout(100)
            assert len(pending) == 1
            denial = context.new_page()
            denial.route("**/api/session", lambda route: route.fulfill(status=401))
            denial.goto(origin + "/web/offline.html")
            denial.wait_for_function(
                "document.querySelector('#status').textContent.includes('Sign in')"
            )
            pending[0].fulfill(status=200, content_type="application/json", body="{}")
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('changed')"
                " || document.querySelector('#status').textContent.includes('cleared')"
            )
            assert page.locator("#source").input_value() == ""
            page.unroute("**/api/decks/deck")
            page.route(
                "**/api/decks/deck",
                lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
            )
            page.reload()
            page.wait_for_function("document.querySelector('#source').value.includes('Original')")
            context.set_offline(True)
            page = context.new_page()
            page.goto(origin + "/web/offline.html")
            page.wait_for_function(
                "document.querySelector('#status').textContent === 'Available on this device.'"
            )
            original_frame = page.frame_locator("#preview")
            original_frame.get_by_text("Original").wait_for()
            assert original_frame.locator("h1").evaluate("e => getComputedStyle(e).color") == (
                "rgb(12, 34, 56)"
            )
            original_frame.locator("img").wait_for()
            assert original_frame.locator("img").evaluate("e => e.complete && e.naturalWidth > 0")
            page.locator("#source").fill("<h1>Offline edit</h1>")
            page.locator("#save-edit").scroll_into_view_if_needed()
            page.locator("#save-edit").click()
            page.wait_for_function(
                "document.querySelector('#status').textContent === 'Saved on this device.'",
                timeout=3000,
            )
            assert page.locator("#status").inner_text() == "Saved on this device."
            page.locator("#status").evaluate("node => node.textContent = ''")
            page.locator("#comment").fill("Fictional comment")
            page.locator("#save-comment").click()
            page.wait_for_function(
                "document.querySelector('#status').textContent === 'Saved on this device.'"
            )
            result = page.evaluate("""async () => {
              const store=OfflineStore.create({enabled:true,scope:'a'.repeat(64)});
              const record=await store.read('deck');store.close();return record.outbox;
            }""")
            assert [row["kind"] for row in result] == ["edit", "comment"]
            assert all(row["anchor"] == "hash-original" for row in result)
            assert all(row["expected_hash"] == "source-original" for row in result)
            assert all(row["receipt"] == "fictional-receipt" for row in result)
            assert [row["body"] for row in result] == ["<h1>Offline edit</h1>", "Fictional comment"]
            page.reload()
            page.wait_for_function(
                "document.querySelector('#source').value.includes('Offline edit')"
            )
            assert page.locator("#outbox li").count() == 2
            assert "Offline edit" in (page.locator("#preview").get_attribute("srcdoc") or "")
            page.wait_for_timeout(500)
            page.frame_locator("#preview").get_by_text("Offline edit").wait_for()
            old_tab = page
            context.set_offline(False)
            page.route(
                "**/offline-snapshot",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"'
                    + "a" * 64
                    + '","receipts":{"cover":"fictional-receipt"}}',
                ),
            )
            page.route(
                "**/api/session",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"' + "a" * 64 + '"}',
                ),
            )
            page.route(
                "**/api/decks/deck",
                lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
            )
            page.route(
                "**/api/decks/deck",
                lambda route: route.fulfill(
                    status=200,
                    json={
                        "id": "deck",
                        "title": "Fictional",
                        "slides": [
                            {
                                "id": "cover",
                                "title": "Cover",
                                "html": "<h1>Offline edit</h1>",
                                "content_hash": "new-content",
                                "expected_hash": "new-source",
                            }
                        ],
                    },
                ),
            )
            calls = {"edit": 0, "comment": 0}

            def edit_response(route: Route) -> None:
                calls["edit"] += 1
                if calls["edit"] == 1:
                    route.fulfill(
                        status=200,
                        json={
                            "status": "conflict",
                            "base": "Original",
                            "current": "Online",
                            "draft": "Offline",
                            "current_expected_hash": "a" * 64,
                            "current_content_hash": "b" * 64,
                        },
                    )
                else:
                    assert (
                        cast(dict[str, object], route.request.post_data_json)["overwrite_expected"]
                        == "a" * 64
                    )
                    route.fulfill(
                        status=200,
                        json={
                            "status": "applied",
                            "content_hash": "new-content",
                            "expected_hash": "new-source",
                        },
                    )

            def comment_response(route: Route) -> None:
                calls["comment"] += 1
                route.fulfill(status=503 if calls["comment"] == 1 else 200, json={})

            page.route("**/offline-edits", edit_response)
            page.route("**/offline-comments", comment_response)
            page.locator("#reconnect").scroll_into_view_if_needed()
            page.locator("#reconnect").click()
            page.wait_for_function("!document.querySelector('#conflict').hidden")
            assert page.locator("#conflict-current").text_content() == "Online"
            page.locator("#use-draft").scroll_into_view_if_needed()
            page.locator("#use-draft").click()
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('paused')"
            )
            assert calls == {"edit": 2, "comment": 1}
            page.locator("#reconnect").scroll_into_view_if_needed()
            page.locator("#reconnect").click()
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('delivered')"
            )
            assert calls == {"edit": 2, "comment": 2}
            page.evaluate("""async () => {
              const store=OfflineStore.create({enabled:true,scope:'a'.repeat(64)});
              await store.queue('deck',{id:'remaining-draft',slide_id:'cover',
                anchor:'hash-original',kind:'comment',body:'Private unfinished draft'});
              store.close();
            }""")

            def assert_blocked_until_authorized() -> None:
                context.set_offline(True)
                blocked_page = context.new_page()
                blocked_page.goto(origin + "/web/offline.html")
                blocked_page.wait_for_function(
                    "document.querySelector('#status').textContent.includes('Reconnect')"
                )
                assert blocked_page.locator("#source").input_value() == ""
                context.set_offline(False)
                verified = context.new_page()
                verified.route(
                    "**/api/session",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"cache_scope":"' + "a" * 64 + '"}',
                    ),
                )
                verified.route(
                    "**/api/decks/deck",
                    lambda route: route.fulfill(
                        status=200, content_type="application/json", body="{}"
                    ),
                )
                verified.goto(origin + "/web/offline.html")
                verified.wait_for_function(
                    "document.querySelector('#source').value.includes('Offline edit')"
                )

            context.set_offline(False)
            page = context.new_page()
            page.route(
                "**/api/session",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"' + "a" * 64 + '"}',
                ),
            )
            page.route(
                "**/api/decks/deck",
                lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
            )
            page.goto(origin + "/web/offline.html")
            page.wait_for_function(
                "document.querySelector('#source').value.includes('Offline edit')"
            )
            page.route("**/api/session", lambda route: route.fulfill(status=401))
            page.evaluate("document.querySelector('#decks').dispatchEvent(new Event('change'))")
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('Sign in')"
            )
            assert page.locator("#source").input_value() == ""
            assert page.locator("#save-edit").is_disabled()
            assert page.locator("#outbox li").count() == 0
            assert page.locator("#decks option").count() == 0
            assert_blocked_until_authorized()
            page = context.new_page()
            page.route(
                "**/api/session",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"' + "a" * 64 + '"}',
                ),
            )
            page.route("**/api/decks/deck", lambda route: route.fulfill(status=403))
            page.goto(origin + "/web/offline.html")
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('drafts remain')"
            )
            assert page.locator("#source").input_value() == ""
            assert_blocked_until_authorized()
            page = context.new_page()
            page.route("**/api/session", lambda route: route.fulfill(status=401))
            page.goto(origin + "/web/offline.html")
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('Sign in')"
            )
            assert page.locator("#source").input_value() == ""
            preserved = page.evaluate("""async () => {
              const store=OfflineStore.create({enabled:true,scope:'a'.repeat(64)});
              const record=await store.read('deck');store.close();return record.outbox.length;
            }""")
            assert preserved == 1
            page = context.new_page()
            page.route(
                "**/api/session",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"cache_scope":"' + "b" * 64 + '"}',
                ),
            )
            page.goto(origin + "/web/offline.html")
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('account changed')"
            )
            assert page.locator("#source").input_value() == ""
            assert page.locator("#decks option").count() == 0
            old_tab.wait_for_function("document.querySelector('#source').value === ''")
            result = page.evaluate("""async () => {
              await OfflineClient.bind('a'.repeat(64));
              let release;const delayed=new Promise(resolve=>{release=resolve;});
              const deck={id:'new-deck',title:'Private',slides:[]};
              const pending=OfflineClient.save(delayed,deck,'/api/session','default')
                .then(()=>false,()=>true);
              await OfflineClient.clear();release({cache_scope:'a'.repeat(64)});
              const blocked=await pending;await OfflineClient.bind('b'.repeat(64));
              return {blocked,index:localStorage.getItem('artifact-studio-offline-index-v1'),
                active:localStorage.getItem('artifact-studio-offline-active-v1')};
            }""")
            assert result == {"blocked": True, "index": None, "active": "b" * 64}
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_keep_online_version_continues_delivering_other_drafts() -> None:
    root = Path(__file__).resolve().parents[1]
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            )
            page = browser.new_page()
            origin = f"http://127.0.0.1:{server.server_port}"
            page.route(
                "**/api/session",
                lambda route: route.fulfill(status=200, json={"cache_scope": "a" * 64}),
            )
            page.route(
                "**/api/decks/deck",
                lambda route: route.fulfill(
                    status=200,
                    json={
                        "id": "deck",
                        "title": "Fictional",
                        "slides": [
                            {
                                "id": "cover",
                                "title": "Cover",
                                "html": "<h1>Online</h1>",
                                "content_hash": "online-content",
                                "expected_hash": "online-source",
                            }
                        ],
                    },
                ),
            )
            page.route(
                "**/offline-snapshot",
                lambda route: route.fulfill(
                    status=200,
                    json={
                        "cache_scope": "a" * 64,
                        "receipts": {"cover": "receipt"},
                    },
                ),
            )
            page.route(
                "**/offline-edits",
                lambda route: route.fulfill(
                    status=200,
                    json={
                        "status": "conflict",
                        "base": "Original",
                        "current": "<h1>Online</h1>",
                        "draft": "<h1>Offline</h1>",
                        "current_expected_hash": "a" * 64,
                        "current_content_hash": "b" * 64,
                    },
                ),
            )
            comments: list[str] = []

            def deliver_comment(route: Route) -> None:
                comments.append(
                    cast(str, cast(dict[str, object], route.request.post_data_json)["message"])
                )
                route.fulfill(status=200, json={})

            page.route("**/offline-comments", deliver_comment)
            page.goto(origin + "/web/offline.html")
            page.evaluate("""async () => {
              const scope='a'.repeat(64);await OfflineClient.bind(scope);
              const store=OfflineStore.create({enabled:true,scope});
              await store.save('deck',{id:'deck',title:'Fictional',workspace:'default',slides:[
                {id:'cover',title:'Cover',html:'<h1>Original</h1>',content_hash:'old',
                  expected_hash:'old-source',receipt:'receipt'}]});
              await store.queue('deck',{id:'edit-001',slide_id:'cover',anchor:'old',
                receipt:'receipt',kind:'edit',body:'<h1>Offline</h1>'});
              await store.queue('deck',{id:'comment-001',slide_id:'cover',anchor:'old',
                receipt:'receipt',kind:'comment',body:'Keep this comment'});store.close();
              localStorage.setItem('artifact-studio-offline-index-v1',
                JSON.stringify([{scope,deck:'deck',title:'Fictional',session:'/api/session'}]));
            }""")
            page.reload()
            page.wait_for_function("document.querySelector('#source').value.includes('Offline')")
            page.locator("#reconnect").click()
            page.wait_for_function("!document.querySelector('#conflict').hidden")
            page.locator("#use-current").click()
            page.wait_for_function(
                "document.querySelector('#status').textContent.includes('delivered')"
            )
            assert comments == ["Keep this comment"]
            assert page.locator("#outbox li").count() == 0
            assert "Online" in page.locator("#source").input_value()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
