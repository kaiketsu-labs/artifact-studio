# Element keyboard controls

Select an object on a slide, or enable **Select** in an HTML artifact and click an element. Right-click opens element actions. **Shift+F10** or the keyboard Menu key also opens the menu; arrows, Home/End and Enter navigate it, and Escape closes it.

| Shortcut | Action |
| --- | --- |
| Ctrl+Z / ⌘Z | Undo the most recent unsent element edit |
| Ctrl+Y or Ctrl+Shift+Z / ⌘Shift+Z | Redo an undone element edit |
| Delete or Backspace | Preview removing the selected element |
| Arrow keys | Nudge the selected element |
| Shift+Arrow keys | Nudge by ten times the normal step |
| F2 or Enter on the canvas | Open the element's text editor, when supported |
| Escape | Cancel the unsent edit; otherwise clear the selection |
| Ctrl+Enter / ⌘Enter in workspace controls | Send the edit explicitly |

These controls apply to the current single-element editor. Multi-selection remains available for feedback; right-clicking a group preserves its targets and offers commenting/selection actions. Markdown text and image regions retain their format-specific selection tools. Text fields and interactive artifacts outside selection mode keep their native context menu.

Edits remain local previews until **Send edit**. Delete hides the object; Undo restores it. Deleting supersedes other unsent edits to that object, and Undo restores those too. Movement and deletion do not write source files or release Batch feedback. Send creates the existing version-bound request for the agent, retaining the original target and hash.

Undo/redo retains forty changes in the current unsent edit. Selecting another object, changing slide/artifact/version, cancelling, or successfully sending clears that history. Undo does not retract sent feedback or reverse an agent's later source revision. Slide thumbnails keep their separate order/deletion history.

Text inputs, notes, chat and contenteditable text keep native text undo, caret movement and deletion. Enter still activates a focused toolbar button. Stop dictation before undoing or deleting an element. While selecting an HTML artifact, the workspace-owned canvas handles keyboard events. Messages from authored frames cannot invoke undo, cancellation, target clearing or submission.

Slides nudge by 0.1% of the canvas (1% with Shift), independent of preview zoom. HTML artifacts nudge by one document CSS pixel (ten with Shift), converting through a measured 2D transform. HTML move requests include the document canvas dimensions and identify that document as their coordinate reference. Unsupported 3D positioning is refused; page-root elements cannot be moved or deleted.
