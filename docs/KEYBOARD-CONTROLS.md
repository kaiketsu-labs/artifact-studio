# Keyboard shortcuts

Shortcuts act on the focused area. Click a slide thumbnail, selected object, chat field or divider first. **⌘** means Command on Mac; **Ctrl** is Control on Windows/Linux. Function keys may require **Fn**. Browser-reserved keys may need the equivalent menu action.

[Files and presentation](#files-and-presentation) · [Slides](#slide-list-and-sorter) · [Objects](#element-keyboard-controls) · [Chat](#chat-notes-and-text) · [Slideshow](#during-a-slideshow) · [Navigation](#menus-projects-and-panels)

## Files and presentation

These shortcuts apply in the presentation editor, outside text entry and modal dialogs. Save can include an unfinished text draft.

| Action | Mac | Windows/Linux |
| --- | --- | --- |
| New presentation | ⌘N | Ctrl+N |
| Import a presentation archive | ⌘O | Ctrl+O |
| Save a presentation archive | ⌘S | Ctrl+S |
| Presentation files / choose export format | ⇧⌘S | Ctrl+Shift+S |
| Present from current slide | ⌘Return | Shift+F5 |
| Present from beginning | ⇧⌘Return or F5 | F5 |
| Open the workspace menu | F10 | F10 |

Save downloads a portable project; normal slide edits already persist locally. In a selected object's controls, ⌘/Ctrl+Enter sends its edit instead of starting a show.

## Slide list and sorter

Focus a slide thumbnail first. The sorter is the grid reached through its icon or by zooming out to 50% or below.

| Shortcut | Action |
| --- | --- |
| ↑ / ↓ | Previous/next visible thumbnail; in the sorter, move between rows |
| ← / → in the sorter | Previous/next thumbnail |
| Home / End | First/last visible thumbnail |
| Shift + navigation keys | Extend the selected range |
| ⌘/Ctrl+A | Select all slides |
| ⌘/Ctrl + click | Add/remove an individual slide from selection |
| Shift + click | Select a range |
| ⌘Shift+N or Ctrl+M | Add a slide after the active slide |
| ⌘/Ctrl+D, also with Shift | Duplicate selected slides |
| Delete / Backspace | Delete selected slides; keep at least one slide |
| ⌘/Ctrl+↑ / ↓ | Move selected slides up/down |
| ⌘/Ctrl+Shift+↑ / ↓ | Move selected slides to the beginning/end |
| ⌘/Ctrl+Z | Undo available slide-structure changes or recover a deleted slide |
| ⌘/Ctrl+Shift+Z or Ctrl+Y | Redo available slide-structure changes |
| Shift+F10 / Menu key | Open the selected slides' context menu |
| Enter in the sorter | Open the selected slide for editing |
| Escape in the sorter | Return to editing |
| Escape while dragging | Cancel the move |

With the editor canvas focused and no object edit active, ←/→ switches slides. ⌘/Ctrl + mouse wheel changes zoom. Undo for adding/duplicating slides is not available; restoring a deletion does not create a redo entry.

In a slide context menu, **Copy** contains image/link copying, **Export** downloads the selected saved slides as HTML or PDF, and **Organize** contains movement, sections and selection. **→** opens a submenu; **←** returns to its parent. **↑/↓** and **Home/End** stay within the current level. **Escape** closes a submenu first, then the main menu and restores thumbnail focus. Submenus expand inline when there is not enough room for a flyout.

Focus a **section header** for ←/→ to collapse/expand it, **F2** to rename it, or **Shift+F10 / Menu** for section actions.

## Element keyboard controls

Select an object on a slide, or enable **Select** in an HTML artifact and click an element. Right-click opens element actions. **Shift+F10** or the keyboard Menu key also opens the menu; arrows, Home/End and Enter navigate it, and Escape closes it.

In an element's alternative chooser, **←/→**, **Home/End**, or **Enter/Space** on an option select a preview. You can keep browsing before clicking outside or closing the chooser to save the last selection. **Escape** cancels it; during animation playback, the first Escape stops playback. A saved choice offers **Undo**.

Drag from empty canvas space to enclose multiple objects. Hold **Shift, Cmd or Ctrl** to add to the selection; **Escape** cancels a drag. Right-click the selection for **Save as image…** (transparent PNG). Images also offer **Save original image…**. Export includes the displayed local edit preview without saving or sending it.

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

While a slide's **rotation handle** is focused, arrows rotate by 1°; Shift+arrows rotate by 15°. In movement/rotation mode, Enter sends the edit and Escape cancels it. Hold ⌘, Ctrl or Shift while clicking slide objects to add/remove selection targets.

The single-element editor supports text, movement and deletion. For multiple selected slide objects, the context menu also offers **Delete selection**, alignment and layer-order actions. Delete and supported layer changes have local previews. Layer previews require positioned sibling objects with automatic layer order; other arrangements record a clearly labeled request without moving the objects. Alignment always records a request without previewing the final layout. Group text editing and keyboard nudging are unavailable.

HTML artifacts keep single-object direct editing; their multi-selection is for group feedback. Markdown text and image regions retain their format-specific selection tools. Text fields and interactive artifacts outside selection mode keep their native context menu.

Edits remain unsent until **Send edit**. A deletion preview hides the object; Undo restores it. In the single-object editor, deleting supersedes other unsent edits to that object, and Undo restores those too. Movement and deletion do not write source files or release Batch feedback. Send creates the existing version-bound request for the agent, retaining the original target and hash.

For slide multi-selection, **Send edit** sends one group-feedback message with the selected objects' original targets and hash. Its JSON message describes `action="edit_selection"` and the requested operations; it does not use a group `edit_intent`. Pending group edits must be sent or cancelled before changing the selection or navigating. Cancel restores the local originals. After sending, **Dismiss preview** or a selection change restores the local preview without retracting the request. Batch still requires **Send batch** before the agent can read it.

The single-object editor retains forty changes in the current unsent edit. Undo/redo for a slide group covers its current unsent selection edit. Selecting another object, changing slide/artifact/version, cancelling, or successfully sending clears that history. Undo does not retract sent feedback or reverse an agent's later source revision. Slide thumbnails keep their separate order/deletion history.

Text inputs, notes, chat and contenteditable text keep native text undo, caret movement and deletion. Enter still activates a focused toolbar button. Stop dictation before undoing or deleting an element. While selecting an HTML artifact, the workspace-owned canvas handles keyboard events. Messages from authored frames cannot invoke undo, cancellation, target clearing or submission.

Slides nudge by 0.1% of the canvas (1% with Shift), independent of preview zoom. HTML artifacts nudge by one document CSS pixel (ten with Shift), converting through a measured 2D transform. HTML move requests include the document canvas dimensions and identify that document as their coordinate reference. Unsupported 3D positioning is refused; page-root elements cannot be moved or deleted.

## Chat, notes and text

| Shortcut | Context and action |
| --- | --- |
| Enter, ⌘Enter or Ctrl+Enter | Send a slide/artifact/design chat message; answer an agent's text question |
| Shift+Enter | Add a line break in chat |
| Escape while recording | Stop dictation |
| ⌘/Ctrl+Z; ⌘/Ctrl+Shift+Z | Native undo/redo while typing, separate from object or slide history |
| ⌘/Ctrl+B / I | Bold/italic in the rich-text rules editor |
| ⌘/Ctrl+K | Add/edit a link in the rich-text rules editor |
| ⌘/Ctrl+S | Save the open design text file while editing |
| ⌘/Ctrl+F | Find/replace in the design source editor |
| ⌘/Ctrl+Z / Shift+Z | Undo/redo source edits before saving |
| Tab / Shift+Tab | Indent/unindent source lines; Escape then Tab exits the editor |

Enter adds a normal line break in notes and other text fields. Sending in Batch mode adds a message to the local batch; use **Send batch** to release it to the agent. Text selection, copy, paste, caret movement and field editing retain their normal browser shortcuts.

## During a slideshow

These controls also work in the self-contained HTML export. Navigation advances/reverses one reveal before moving between slides; normal navigation skips hidden slides.

| Shortcut | Action |
| --- | --- |
| → / ↓ / Page Down | Next reveal or slide |
| ← / ↑ / Page Up / Backspace / Delete | Previous reveal or slide |
| Space / Enter or another ordinary character key | Next reveal or slide |
| Home / End | First/last visible slide |
| Escape | End the show; a hosted show returns to the editor at the current slide |
| Tab, then Enter/Space | Focus and activate presentation controls |

Clicking the slide background also advances. Buttons, links, text entry, open dialogs and system/browser modifier shortcuts retain their own behavior. In an exported file, ending returns to that file's preview view. There, **F** toggles fullscreen, **N** opens notes and **S** opens sources; these letters advance during an active show. Audience-only windows omit private note/source commands. Exports contain no private speaker notes.

## Menus, projects and panels

| Focused area | Shortcut | Action |
| --- | --- | --- |
| Library artifact, project/folder, slide or object | Shift+F10 / Menu key | Open its context menu |
| Menu or project/deck picker | ↑ / ↓, Home / End | Move between available items |
| Menu or picker button | ↑ / ↓ | Open its list |
| Workspace menu or deck picker | Type a name's first letters | Jump to a matching action/item |
| Appearance menu | → / ← | Open the submenu / return |
| Menu, picker or dialog | Escape | Close; return focus where supported |
| Project/folder row | Alt+↑ / ↓ | Move before/after its sibling |
| Nested project/folder row | Alt+← | Move out one level, after its parent |
| Version-history thumbnail | ↑ / ↓ | Select the previous/next saved version; scroll it into view |
| Side-panel edge handle | ← / → | Resize; collapse when moved past its limit |
| Side-panel edge handle | Enter | Collapse/reopen the panel |
| Chat-width divider | ← / →, with Shift for larger changes | Resize the conversation panel |
| Chat-width divider | Home / End | Smallest/largest allowed width |
| Chat-width divider | Enter | Reset the preferred width |
| Clean View | Escape | Restore the surrounding workspace |
| Compact chat/sidebar drawer | Escape | Close the drawer |
| Animation preview | Escape | Stop playback |

Use Tab/Shift+Tab to move between controls and Enter/Space to activate buttons. Arrow-key actions depend on focus; typing in a field does not rearrange slides or delete objects.
