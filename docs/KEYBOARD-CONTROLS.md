# Element keyboard controls

Select an object on a slide, or enable **Select** in an HTML artifact and click an element. Right-click opens element actions. **Shift+F10** or the keyboard Menu key also opens the menu; arrows, Home/End and Enter navigate it, and Escape closes it.

| Shortcut | Action |
| --- | --- |
| Ctrl+Z / ⌘Z | Undo the most recent unsent element edit |
| Ctrl+Y or Ctrl+Shift+Z / ⌘Shift+Z | Redo an undone element edit |
| Delete or Backspace | Preview removing the selected element, or selected slide objects |
| Arrow keys | Nudge a single selected element |
| Shift+Arrow keys | Nudge a single element by ten times the normal step |
| F2 or Enter on the canvas | Open the element's text editor, when supported |
| Escape | Cancel the unsent edit; otherwise clear the selection |
| Ctrl+Enter / ⌘Enter in workspace controls | Send the edit explicitly |

The single-element editor supports text, movement and deletion. For multiple selected slide objects, the context menu also offers **Delete selection**, alignment and layer-order actions. Delete and supported layer changes have local previews. Layer previews require positioned sibling objects with automatic layer order; other arrangements record a clearly labeled request without moving the objects. Alignment always records a request without previewing the final layout. Group text editing and keyboard nudging are unavailable.

HTML artifacts keep single-object direct editing; their multi-selection is for group feedback. Markdown text and image regions retain their format-specific selection tools. Text fields and interactive artifacts outside selection mode keep their native context menu.

Edits remain unsent until **Send edit**. A deletion preview hides the object; Undo restores it. In the single-object editor, deleting supersedes other unsent edits to that object, and Undo restores those too. Movement and deletion do not write source files or release Batch feedback. Send creates the existing version-bound request for the agent, retaining the original target and hash.

For slide multi-selection, **Send edit** sends one group-feedback message with the selected objects' original targets and hash. Its JSON message describes `action="edit_selection"` and the requested operations; it does not use a group `edit_intent`. Pending group edits must be sent or cancelled before changing the selection or navigating. Cancel restores the local originals. After sending, **Dismiss preview** or a selection change restores the local preview without retracting the request. Batch still requires **Send batch** before the agent can read it.

The single-object editor retains forty changes in the current unsent edit. Undo/redo for a slide group covers its current unsent selection edit. Selecting another object, changing slide/artifact/version, cancelling, or successfully sending clears that history. Undo does not retract sent feedback or reverse an agent's later source revision. Slide thumbnails keep their separate order/deletion history.

Text inputs, notes, chat and contenteditable text keep native text undo, caret movement and deletion. Enter still activates a focused toolbar button. Stop dictation before undoing or deleting an element. While selecting an HTML artifact, the workspace-owned canvas handles keyboard events. Messages from authored frames cannot invoke undo, cancellation, target clearing or submission.

Slides nudge by 0.1% of the canvas (1% with Shift), independent of preview zoom. HTML artifacts nudge by one document CSS pixel (ten with Shift), converting through a measured 2D transform. HTML move requests include the document canvas dimensions and identify that document as their coordinate reference. Unsupported 3D positioning is refused; page-root elements cannot be moved or deleted.
