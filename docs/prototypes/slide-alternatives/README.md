# Slide design alternatives — UX draft

An interactive, fictional editor prototype for parallel **whole-slide designs**. Open `index.html` through a server rooted at the repository, for example:

```sh
uv run python -m http.server 8791 --bind 127.0.0.1
# http://127.0.0.1:8791/docs/prototypes/slide-alternatives/
```

The chooser runs entirely in memory. It makes no product API calls, generates no designs and saves no presentation changes. The surrounding editor is illustrative. Production controllers and agent tool schemas are unchanged.

## Proposed experience

1. **Discover.** The slide overview shows “3 alternatives”; the editor's Designs button counts the original plus those three. The agent's chat reply contains a visual entry point to the options. Alternatives are parallel design directions, separate from chronological version history.
2. **Preview.** Click a named thumbnail or use arrow keys, Home and End. A blue selection identifies the preview. “In slide” continues to mark the actual chosen design, including on its thumbnail. The preview state and **Use this design** stay beside the canvas even if the options row is collapsed. Escape returns to the chosen design.
3. **Compare.** Compare the current design with a proposal, or any two proposals. Both previews share dimensions and alignment. Either visible design can be chosen directly. On phones the previews stack; short screens use the two dropdowns instead of squeezing in another thumbnail row. The decision controls remain available while previews scroll.
4. **Choose.** **Use this design** changes the slide's chosen design, updates its overview thumbnail, preserves every proposal and offers **Undo**. It returns the slide to review; choosing a design and approving a slide are separate decisions. Approval is hidden while previewing. Present uses the chosen design.
5. **Refine.** A chat context chip names the design being previewed. In the real feature, feedback and questions must carry that design's identity so an agent refines the intended option. Human speaker notes stay attached to the slide. Navigating to another slide ends a temporary preview.

![Previewing Editorial while the original remains in the slide.](screenshots/01-preview.png)

![Comparing two proposals before choosing either.](screenshots/02-compare.png)

[Choice and Undo](screenshots/03-chosen.png) · [Dark appearance](screenshots/04-dark.png) · [Phone preview](screenshots/05-mobile.png) · [Phone comparison](screenshots/06-mobile-compare.png)

## Iterations made in the browser

- Reduced the thumbnail row's width to preserve space for the slide and expanded notes.
- Hid Approval during temporary previews to keep the decision unambiguous.
- Extended comparison from current-versus-proposal to any pair, with a direct choice of either design.
- Aligned both comparison canvases and kept the decision controls visible on short screens.
- Added dropdown switching on compact comparisons, kept the current-design marker visible, and corrected accessible names and muted footer contrast.

## Behavior to retain in implementation

Agent publication must add proposals without switching the chosen design, the user's preview or keyboard focus. Each proposal needs a stable identity, a short name, a renderable preview, its source slide revision and an optional brief rationale. Generation can expose ready proposals while others finish; failures should stay local to their proposal.

Choosing must check the slide revision before applying a proposal. If the underlying slide changed, show that conflict and let the reviewer inspect the latest slide before replacing it. Keep the original and remaining options available. Preserve human notes, discussion and pending feedback; bind element anchors and proposal feedback to their actual design revision. Changes to a chosen design must not silently rewrite another proposal. Presentation/export uses the chosen design, never a temporary preview.

## Components next

Reuse **discover → preview → compare → choose** for a selected component: “Chart · 3 options”, anchored to that selection. Preview within the full slide so surrounding context stays visible. Compare at the same scale; choosing changes only that component and offers Undo.

Component proposals must be tied to one chosen slide design and a stable component/group identity. A chart from one whole-slide proposal cannot be assumed to fit another proposal. Free-form HTML compositions also need a reliable selection boundary before component alternatives are safe. Start with whole-slide alternatives, validate that experience, then introduce component alternatives on that foundation.

## Verification

Browser review covers mouse and keyboard switching, safe preview, either-side comparison choices, apply/undo, preserved notes, retained alternatives, presentation using the chosen design, and light/dark layouts at 1440, 768, 390 and 320 pixels. Axe-core 4.14.0 reported no A/AA violations across 17 sampled states; this is not a complete accessibility certification. The 320px comparison keeps its choice button inside the viewport. The standalone download was also exercised offline without external requests.

Screenshots are direct Chromium captures of this prototype with fictional data; they are not images of an implemented product feature. [Capture provenance and sampled results](captures.json) accompany the six images. Physical-device and user usability testing remain follow-up work.
