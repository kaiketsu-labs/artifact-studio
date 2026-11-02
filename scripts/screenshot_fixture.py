"""Original fictional slides for documentation captures; no user collection is read."""

import json
import shutil

from artifact_studio.models import AgentContext, Deck, DeckSection, Slide, Source
from tests.design_factory import TEMPLATE_ID, powerpoint_source
from tests.integration.support import DECK_ID, PROJECT, RunningApp

STYLE = """
.slide{font-family:Arial,Helvetica,sans-serif;color:#132944;background:#f6f8fc;
 padding:5.8% 6.4%;position:relative;letter-spacing:-.035em}
.slide h1,.slide h2,.slide p{margin:0;color:inherit}
.slide h1{font-size:5.3cqw;line-height:1.05;font-weight:750;letter-spacing:-.055em}
.slide h2{font-size:4.15cqw;line-height:1.12;letter-spacing:-.05em}
.slide p{font-size:1.65cqw;line-height:1.5;letter-spacing:-.015em}
.slide .eyebrow{font-size:1cqw;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
 margin-bottom:2.4cqw;color:#526d8b}
.slide .sub{color:#526d8b;margin-top:2cqw;max-width:56cqw}
.slide .foot{position:absolute;bottom:3cqw;left:6.4cqw;right:6.4cqw;display:flex;
 justify-content:space-between;font-size:.85cqw;letter-spacing:.02em;color:#60758d}
.slide .cards{display:flex;gap:1.3cqw;margin-top:4cqw}
.slide .card{flex:1;padding:2.3cqw;border-radius:1.5cqw;background:#e8eef7;min-height:19cqw}
.slide .card strong{display:block;font-size:2.2cqw;margin:2.4cqw 0 .8cqw}
.slide .card p{font-size:1.65cqw;line-height:1.4}
.slide .num{font-size:1.05cqw;font-weight:700;opacity:.75}
.slide .blue{background:#315bea;color:#fff}.slide .mint{background:#c1efdd;color:#173c38}
.slide.cover{background:#132944;color:#fff}
.slide.cover .eyebrow{color:#aabedf}.slide.cover h1{max-width:58cqw;font-size:6.5cqw}
.slide.cover .sub{color:#c1d0e4;font-size:1.8cqw;max-width:50cqw}
.slide.cover .foot{color:#aabedf}
.slide .brand-mark{position:absolute;right:8cqw;top:16cqw;width:19cqw;height:19cqw;
 border-radius:4.2cqw;box-shadow:0 2cqw 6cqw #06142c44}
.slide .pair{display:flex;gap:2cqw;margin-top:3.5cqw}
.slide .pair>div{flex:1;border-radius:1.4cqw;padding:2.5cqw;background:#e8eef7;min-height:22cqw}
.slide .pair .after{background:#fff;border:1px solid #d8e1ee}
.slide .small-label{font-size:1cqw;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
 color:#60758d;margin-bottom:2cqw}
.slide .line{height:.9cqw;background:#c5d1e1;border-radius:1cqw;margin-bottom:1.2cqw}
.slide .big-idea{font-size:3.3cqw;line-height:1.1;font-weight:700;max-width:26cqw}
.slide .underline{height:.6cqw;width:6cqw;background:#315bea;margin-top:2cqw;border-radius:1cqw}
.slide.ready{background:#315bea;color:#fff}.slide.ready .eyebrow,.slide.ready .sub,
.slide.ready .foot{color:#d7e0ff}.slide.ready h1{font-size:7cqw;max-width:70cqw}
.slide .bars{display:flex;gap:2.3cqw;align-items:end;height:19cqw;margin-top:2.4cqw}
.slide .bar{width:12cqw;border-radius:1cqw 1cqw .25cqw .25cqw;background:#315bea;
 position:relative;transition:height .25s}
.slide .bar span{position:absolute;bottom:1cqw;left:1.2cqw;color:white;font-size:1.2cqw}
.slide .compare{position:absolute;right:6.4cqw;top:6cqw;border:0;background:#132944;
 color:white;font-size:1.1cqw;padding:1cqw 1.8cqw;border-radius:2cqw}
.slide .chart-label{margin-top:1.5cqw;color:#60758d;font-size:1.1cqw}
"""


def article(body: str, number: int, kind: str = "") -> str:
    """Keep content consistent in editor, thumbnails, captures and presenter view."""
    return (
        f'<article class="slide {kind}">{body}'
        '<footer class="foot"><span>Artifact Studio / A clearer way to work</span>'
        f"<span>{number:02}</span></footer></article>"
    )


def seed(app: RunningApp) -> Deck:
    """Replace only the integration helper's disposable deck and design."""
    cover = article(
        '<p class="eyebrow">Ideas, made clear</p><h1>A clearer<br>way to work.</h1>'
        '<p class="sub">Draft together. Refine in context.<br>Present with confidence.</p>'
        '<img class="brand-mark" src="assets/studio-mark.png" alt="Artifact Studio">',
        1,
        "cover",
    )
    workflow = article(
        '<p class="eyebrow">From first thought to final slide</p>'
        '<h2>Good slides happen<br>in the loop.</h2><div class="cards">'
        '<div class="card"><span class="num">01</span><strong>Brief</strong>'
        "<p>Bring your audience,<br>ideas and references.</p></div>"
        '<div class="card blue"><span class="num">02</span><strong>Draft</strong>'
        "<p>Let your agent turn<br>the story into slides.</p></div>"
        '<div class="card mint" id="review-step" aria-label="Review step">'
        '<span class="num">03</span><strong>Review</strong>'
        "<p>Point to the change.<br>Make it better, together.</p></div></div>",
        2,
    )
    story = article(
        '<p class="eyebrow">Make room for the message</p><h2>One slide. One clear idea.</h2>'
        '<div class="pair"><div><p class="small-label">Before</p>'
        '<div class="line"></div><div class="line" style="width:85%"></div>'
        '<div class="line" style="width:92%"></div><div class="line" style="width:70%"></div>'
        '<div class="line" style="width:88%"></div><div class="line" style="width:60%"></div>'
        '</div><div class="after"><p class="small-label">After</p>'
        '<div class="big-idea">Less noise.<br>More meaning.</div>'
        '<div class="underline"></div></div></div>',
        3,
    )
    chart = article(
        '<p class="eyebrow">A little interaction</p><h2>Let the story move.</h2>'
        '<div class="bars"><div class="bar" style="height:45%"><span>Brief</span></div>'
        '<div class="bar" style="height:65%;background:#7795f3"><span>Draft</span></div>'
        '<div class="bar" style="height:90%;background:#339d7e"><span>Review</span></div></div>'
        '<p class="chart-label" id="chart-label">Illustrative workflow</p>',
        4,
    )
    interactive = (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "*{box-sizing:border-box}body{margin:0}.slide{height:100vh}"
        + STYLE
        + "</style></head><body>"
        + chart.replace(
            '<div class="bars">',
            '<button class="compare" id="compare">Compare</button><div class="bars">',
        )
        + """<script>
        document.getElementById('compare').addEventListener('click',()=>{
          const bars=document.querySelectorAll('.bar');
          [75,90,60].forEach((value,index)=>bars[index].style.height=value+'%');
          document.getElementById('chart-label').textContent=
            'Comparison view · illustrative values';
          document.getElementById('compare').textContent='Compare again';
        });
        </script></body></html>"""
    )
    deck = Deck(
        id=DECK_ID,
        title="A clearer way to work",
        design_id="studio-blue",
        slides=[
            Slide(
                id="cover",
                title="A clearer way to work",
                html=cover,
                notes="Open with the idea: a presentation is a conversation, not a hand-off.",
            ),
            Slide(
                id="workflow",
                title="Good slides happen in the loop",
                html=workflow,
                notes="• Ask a teammate for one concrete change.\n"
                "• Show that speaker notes stay private.",
                agent=AgentContext(summary="Three steps keep the review loop visible."),
            ),
            Slide(
                id="story",
                title="One slide. One clear idea.",
                html=story,
                notes="Pause on the contrast. Give the main idea room to breathe.",
            ),
            Slide(
                id="interactive",
                title="Let the story move",
                html=chart,
                interactive_html=interactive,
                notes="Click Compare to demonstrate an offline interaction. Values are fictional.",
            ),
            Slide(
                id="ready",
                title="Ready for the room",
                html=article(
                    '<p class="eyebrow">Your story, ready to share</p>'
                    "<h1>Ready for<br>the room.</h1>"
                    '<p class="sub">Keep your notes close.<br>Let the message take the stage.</p>',
                    5,
                    "ready",
                ),
                notes="Invite a question, then share the next step.",
            ),
            Slide(
                id="appendix",
                title="Working notes",
                hidden=True,
                html=article(
                    '<p class="eyebrow">Appendix</p><h2>Space for the details.</h2>'
                    '<p class="sub">Keep supporting material nearby.<br>'
                    "Show it when the conversation needs it.</p>",
                    6,
                ),
                notes="Optional supporting slide; hidden from normal audience navigation.",
            ),
        ],
        sections=[
            DeckSection(
                id="story-section", title="The story", slide_ids=["cover", "workflow", "story"]
            ),
            DeckSection(
                id="present-section",
                title="In the room",
                slide_ids=["interactive", "ready", "appendix"],
            ),
        ],
    )
    for folder in (app.store.folder(DECK_ID), app.store.folder(TEMPLATE_ID)):
        with (folder / "theme.css").open("a") as theme:
            _ = theme.write(STYLE)
        _ = shutil.copy2(PROJECT / "web/brand/mark.png", folder / "assets/studio-mark.png")
    template = deck.model_copy(deep=True)
    template.id = TEMPLATE_ID
    template.title = "Studio Blue"
    template.slides[0].sources = [Source(label="Studio Blue master", url="Master.pptx")]
    app.store.write(TEMPLATE_ID, template)
    app.store.write(DECK_ID, deck)
    design_folder = app.store.folder(TEMPLATE_ID)
    manifest = json.loads((design_folder / "design.json").read_text())
    manifest.update(
        id="studio-blue", name="Studio Blue", description="Original fictional documentation design."
    )
    _ = (design_folder / "design.json").write_text(json.dumps(manifest))
    _ = (design_folder / "Master.pptx").write_bytes(powerpoint_source("Studio Blue demo reference"))
    app.store.set_default_design("studio-blue")
    for slide in (deck.slides[0], deck.slides[2]):
        app.store.approve(DECK_ID, slide.id, app.store.content_hash(DECK_ID, slide), True)
    for slide in deck.slides:
        app.store.mark_slide_seen(DECK_ID, slide.id, app.store.content_hash(DECK_ID, slide))
    return deck
