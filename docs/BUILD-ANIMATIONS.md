# Build animations

The shared runtime animates ordinary HTML and inline SVG without slide scripts. Use a numbered `data-build` group for each advance. Keep exact final values in the markup so editor previews, exports, print and reduced-motion presentation remain readable.

```html
<div data-build="1" data-animate-duration="1200">
  <div style="width:70%;height:3cqw;background:#2864db"
       data-animate-grow="x"></div>
  <span data-count-to="9568" data-count-prefix="$">$9,568</span>
</div>
<p data-build="1" data-animate-delay="1200">Result after the bar</p>
<p data-build="2" data-reveal="blur">The next figure</p>
```

The bar and counter start together when build 1 is revealed. The result appears after 1,200 ms. The last paragraph is a blurred, noninteractive teaser until build 2. Choose timing that serves the explanation. On a dense slide, reveal meaningful groups in the order of the explanation. Animate one notable chart element or result when it helps direct attention, keeping the rest stable. Avoid decorative motion, excessive clicks and animation on every slide.

| Attribute | Behavior |
| --- | --- |
| `data-build="N"` | Positive numbered reveal stage; enclosing stages must also be revealed. |
| `data-animate-grow="x"` | Reveals a bar from left to right using clipping, preserving its size and transforms. Works on HTML and SVG. |
| `data-count-to="9568"` | Animates a text-only element from zero to the numeric target. Put the exact final display text inside it. |
| `data-count-from="0"` | Optional starting number. |
| `data-count-decimals="0"` | Optional decimal places, 0–20; otherwise inferred from the target. |
| `data-count-prefix="$"` / `data-count-suffix="%"` | Optional text around intermediate numbers. |
| `data-animate-duration="1200"` | Nonnegative milliseconds, inherited from the nearest ancestor; default 800. |
| `data-animate-delay="1200"` | Nonnegative milliseconds from this build's activation, inherited from the nearest ancestor; default zero. An animation also waits for any remaining delay of enclosing build groups. An already visible parent adds no new wait. Delays are not added together. |
| `data-reveal="blur"` | Keeps an unrevealed build visibly blurred instead of invisible. |

Counters accept text-only spans, SVG `text` or `tspan` elements. Intermediate numbers use the document language, falling back to English. Completion restores the authored text exactly, including its minus sign, spacing or notation. Do not nest markup inside a counter.

Growth preserves authored transforms. If a bar already has a clip path, put `data-animate-grow="x"` on an unclipped inner wrapper; the runtime skips growth on an already clipped element. SVG growth uses its fill box. Avoid applying growth to an entire chart if only the bar should move.

Backward navigation resets later builds; returning to a slide replays its animations. Repeated rendering of the same build does not restart it. Reduced motion shows final values without delays. Editor/all-visible previews and print show every build with exact final values. Printing temporarily finishes animations, then resumes their prior presentation state.

Unrevealed or delayed builds are hidden from assistive technology and keyboard focus, including blurred teasers. Authored accessibility attributes and filters are restored when revealed. Private notes and feedback never form part of this animation markup.

Visually review the initial view, each reveal at its start, intermediate motion, delayed boundaries, counter/bar synchronization and the completed slide at presentation size. Use the stage/time captures described in [visual review](VISUAL-REVIEW.md), plus permitted live playback for entry, continuity and replay. Check the final chart against its sources. A correct target number alone does not verify the rendered chart.
