# Referencing work in chat

Type **@** in presentation or artifact chat. Search by a project, artifact,
presentation or slide title; favorite-slide descriptions are searchable too.
Choose a suggestion to attach its saved context.

- **↑ / ↓** selects a suggestion; **Enter** attaches it without sending the message.
- **Tab** browses into the selected project or presentation. Continue typing after
  `/` to narrow the path, for example `@Fieldnotes/Launch brief`.
- **Escape** dismisses suggestions. Enter sends normally after the picker closes.
- Hover over a suggestion for a preview next to the cursor. Keyboard selection
  shows the preview beside the list. Touch selection works without hover.

Artifact and presentation previews reuse saved library covers. When a cover is
being updated, the previous saved preview can stay visible; moving away or
dismissing suggestions stops waiting for the update. Individual slides retain
their live slide preview. The title and reference remain available if a cover
cannot be rendered.

The selected path is inserted into the message, with quotes added automatically
for names containing spaces or punctuation. A reference chip shows what will be
included. Remove it with its **×**. Repeating the same version in one message
reuses its attachment. Up to five attachments fit in one message.

You can still click **★ beside the paperclip** to choose several favorite slides
at once, including slides from other presentations.

## What the agent receives

| Selected item | Saved context |
| --- | --- |
| Project | The project brief and an artifact index, including IDs and versions. Select individual artifacts when their source is needed. |
| Artifact | Its authored brief and audience files, with text sources first, local dependencies included, and external dependencies and excluded paths identified. |
| Presentation | The full audience presentation, including embedded local assets and interactive dependencies. |
| Slide | The chosen slide and its optional favorite description, with theme and local assets. It does not need to be starred. |

Private feedback, unreleased Batch messages and speaker notes are excluded.
References preserve the selected version after the source is renamed, moved or
edited. Changes while a reference is being selected require choosing it again.
Readable paths are labels; stable IDs distinguish identical titles. Selecting a
reference does not grant permission to edit its source.

References use the existing feedback attachment delivery. Local agents read the
saved path. Hosted agents use `read_feedback_attachment` on the destination
chat's released message. Source content is loaded only when selected; the
workspace reference catalog is not added to agent startup context.

![A slide preview next to the pointer over a path suggestion](screenshots/chat-reference-hover.png)
![Selected paths and reference chips in chat](screenshots/chat-reference-chips.png)
