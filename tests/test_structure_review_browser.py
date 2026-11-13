"""Exercise the structure review with fictional collaborators."""

from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


def test_conflict_preserves_decision_and_requires_review() -> None:
    """A concurrent reorder cannot silently accept an outdated proposal."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        _exercise(page)
        browser.close()


def _exercise(page: Page) -> None:
    """Drive the review dialog with fake API responses."""
    page.set_content('<button id="trigger" hidden></button><dialog id="review"></dialog>')
    page.add_script_tag(path=str(Path(__file__).parents[1] / 'web/structure-review.js'))
    page.evaluate("""() => {
      window.ArtifactSession = {workspace: 'fiction'};
      if (!window.crypto.randomUUID) window.crypto.randomUUID = () => 'fictional-request-id';
      window.snapshot = {slide_ids:['a','b'], sections_hash:'one',
        slides:[{id:'a',title:'Alpha'},{id:'b',title:'Beta'}],
        proposals:[{id:'p', summary:'Proposed sequence', base_slide_ids:['a','b'],
          base_sections_hash:'one', proposed_slide_ids:['b','a'],
          missing:[], extra:[], status:'pending'}]};
      window.calls = [];
      window.review = StructureReview.create({button:document.querySelector('#trigger'),
        dialog:document.querySelector('#review'), escape:value => String(value),
        api:async (path, body) => {
          if (!body) return structuredClone(window.snapshot);
          window.calls.push(body);
          if (window.calls.length === 1) {
            window.snapshot.slide_ids=['b','a']; window.snapshot.sections_hash='two';
            throw Object.assign(new Error('Conflict'),{status:409});
          }
        }});
    }""")
    page.evaluate("() => window.review.sync('deck', true)")
    page.get_by_role('button', name='Review slide structure (1)').click()
    page.get_by_label('Accept proposal').check()
    page.get_by_label('Message to the proposing agent').fill('Please check the sequence.')
    page.get_by_role('button', name='Send decision').click()
    expect(page.get_by_text('The presentation or proposal changed.', exact=False)).to_be_visible()
    assert page.get_by_label('Accept proposal').is_disabled()
    assert (
        page.get_by_label('Message to the proposing agent').input_value()
        == 'Please check the sequence.'
    )
    page.get_by_label('Request changes').check()
    page.get_by_role('button', name='Send decision').click()
    calls = page.evaluate('window.calls')
    assert calls[0]['expected_slide_ids'] == ['a', 'b']
    assert calls[1]['expected_slide_ids'] == ['b', 'a']
    assert calls[1]['decision'] == 'request_change'


def test_deck_switch_during_submission() -> None:
    """A late response cannot alter the newly opened deck's review."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content('<button id="trigger" hidden></button><dialog id="review"></dialog>')
        page.add_script_tag(path=str(Path(__file__).parents[1] / 'web/structure-review.js'))
        page.evaluate("""() => {
          window.ArtifactSession = {workspace:'fiction'};
          if (!window.crypto.randomUUID) window.crypto.randomUUID = () => 'fictional-request-id';
          window.review = StructureReview.create({button:document.querySelector('#trigger'),
            dialog:document.querySelector('#review'), escape:value => String(value),
            api:async (path, body) => {
              if (body) return new Promise(resolve => { window.finishRequest = resolve; });
              const name = path.includes('/artifacts/first/') ? 'First' : 'Second';
              return {slide_ids:['a'], sections_hash:'one', slides:[{id:'a',title:name}],
                proposals:[{id:'p', summary:name, base_slide_ids:['a'],
                  base_sections_hash:'one', proposed_slide_ids:['a'],
                  missing:[], extra:[], status:'pending'}]};
            }});
        }""")
        page.evaluate("() => window.review.sync('first', true)")
        page.get_by_role('button', name='Review slide structure (1)').click()
        page.get_by_label('Keep current order').check()
        page.get_by_role('button', name='Send decision').click()
        page.wait_for_function('window.finishRequest !== undefined')
        page.evaluate("() => window.review.sync('second', true)")
        page.get_by_role('button', name='Review slide structure (1)').click()
        expect(page.locator('.structure-review-body > p').filter(has_text='Second')).to_be_visible()
        page.evaluate('() => window.finishRequest()')
        expect(page.locator('.structure-review-body > p').filter(has_text='Second')).to_be_visible()
        expect(page.get_by_role('dialog')).to_be_visible()
        browser.close()
