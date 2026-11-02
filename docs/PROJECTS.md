# Projects and exports

Open **Menu → Project files**. Your existing presentations already persist locally between agent sessions; project files provide a portable copy you can reopen in another Artifact Studio workspace.

| Format | Purpose | Included |
| --- | --- | --- |
| `.artifact-studio.zip` | Continue editing later or elsewhere | Current slides and order, hidden flags, assets, themes, brief attachments, context documents, speaker notes, chat history and replies, approvals, unread markers, revision history, Live/Batch delivery preference, unreleased Batch comments and unfinished browser/presenter note drafts. |
| `.html` | Share or present an audience version offline | All slides, local images and fonts, CSS animations and interactive reveal steps. Normal navigation skips hidden slides; they remain available through See all slides. No private notes, chats or agent explanations. |
| PDF | Distribute a static audience version | Every slide, including hidden slides, with reveal steps visible. No private notes or chats. |

## Save and reopen a project

Choose **Save project** to download the selected deck. **Open project** accepts that file and restores its selected slide, unsent comments, unsaved notes and open supporting panels. Finish or cancel an element edit before saving or opening a project; that provisional edit is not silently discarded.

If a deck with the same ID exists, opening the file creates a separate **(copy)**. Existing work is never overwritten. Chats keep their original timestamps, slide context, reply relationships and release state. Approvals and unread markers retain their original content checks, including asset timestamps. The application rebuilds executable viewer code from the installed runtime while preserving inert design settings and the slide data.

Recoverable slide deletions and completed structural actions travel with the project. The trash icon in the restored workspace can recover deleted groups with their original IDs, notes, visibility and conversation history. A restored copy has its own history; recovering a slide there does not change the original presentation. Interrupted prepared actions are recovered before saving. If the canonical file diverged independently, saving refuses the conflict rather than replacing that work.

The bundle stores copies of workspace instructions and learned preferences under `project-context/` in the restored folder. These are reference snapshots. The destination workspace's `INSTRUCTIONS.md` remains primary and is not replaced by imported files. A shared design pack is not registered or replaced when a presentation is opened; its own copied styling and assets are sufficient to edit and present it.

Live agent leases and external harness chats are not portable. Connect your agent normally, copy the selected project's agent prompt, and have it read `get_working_context`, `get_presentation` and `read_feedback`. Saved Batch comments remain withheld until you release them.

The versioned archive contains `project.json` and a hashed `files/` inventory. Limits are **100 MB compressed**, **300 MB expanded** and **3000 files**. Assets must be actual files inside the project; copy symbolic-link targets into the project before saving. Environment/cache folders, hidden root configuration and credential-named sidecars outside asset trees are excluded. Complete asset and brief-attachment trees, including hidden files used by review hashes, are retained.

## Share an artifact or project

Use the square-and-arrow **Share** action on an artifact card, in its header, or in the presentation toolbar. Open a project in the library to share all its artifacts as a ZIP.

When a presentation is included, select **HTML** or **PDF** first. HTML is one offline file with local assets, animations and interactions. PDF is a rendered document containing every slide, including hidden slides. One format applies to all presentations in a project ZIP; other artifacts retain their original format or complete multi-file bundle. Project folders have readable, unique names.

Choose **Outlook** to open a new message with the prepared file attached. Add recipients, a subject and your message in Outlook, then send it yourself. On macOS this uses Outlook’s native file-open handoff, including New Outlook; no AppleScript automation or default-mail-client change is required. **Save file** downloads the same attachment. Where the browser supports file sharing, **More…** offers its system share sheet.

Shares use saved artifact revisions. Speaker notes, review chat, workspace rules and Studio review metadata stay out of presentation audience exports. Generic bundles omit recognized private/configuration paths and list exclusions in the Share panel; a detected reference to an excluded file stops the export. Authored files can still contain sensitive content. Remote dependencies in generic HTML are not downloaded automatically.

Prepared files stay in local memory for up to one hour, are limited to 100 MB, and are never uploaded by sharing preparation. Outlook receives a local attachment copy in the system temporary directory so it can finish reading the file. A project sharing ZIP is for distribution; it does not restore review state. Use **Save project** for an editable presentation archive.

## HTML

**Export HTML** downloads a single file with local image/font dependencies embedded as data URLs. CSS imports, responsive images, SVG references and CSS backgrounds are resolved locally. External hyperlinks remain links. Remote rendering assets must first be downloaded into the project; missing or external asset dependencies produce an actionable export error instead of an incomplete file.

Open the downloaded file in a normal browser. It starts in the slideshow and retains navigation, progress and reveal controls. CSS animations remain interactive. Private speaking context and discussions stay in the project file rather than this audience copy.

## PDF

**Export PDF** opens a print layout and requests the system print dialog after images and fonts are ready. Select **Save as PDF** as the destination. The fallback **Save as PDF** button reopens the dialog if the browser blocks automatic printing. The layout uses 16:9 pages with zero page margins and print background colors enabled in CSS. Browser settings can override paper size, headers/footers or background printing; use the document's page size and inspect the print preview.

PDF deliberately includes **all slides**, including hidden ones. Builds are fully revealed and animations are frozen into a static view. The print controls are hidden by the print stylesheet. The HTML print-layout preview and actual native PDF output are separate verification checks.
