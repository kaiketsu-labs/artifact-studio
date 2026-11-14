# Voice input and selection context

Use the microphone beside an editable text field to dictate with your browser's speech service. Start explicitly. While recording, use Cancel to discard that recording, Stop to keep its text as a draft, or Send to finish recognition and submit deliberately. Speech results alone never submit a form. Speech recognition availability varies by browser, device, language and microphone permissions. Unsupported browsers keep ordinary typing available. Artifact Studio requires no transcription API key and receives text, not microphone audio; the browser's provider may process audio remotely. This is not an offline-transcription guarantee.

Send waits for a clean recognition end. Cancelling during that wait prevents sending; a speech error, shutdown timeout or unfinished transcript also leaves the draft unsent for review. Confirmed text is retained unless you explicitly cancel that recording.

Microphones cover feedback, speaker notes, hosted presenter notes, presentation titles/briefs, design requests, project/artifact forms, search, question answers, and editable workspace rules. Rich rules use the editor's text API; inline slide text editing keeps its existing explicit Send/Cancel flow. Read-only fields do not offer editing controls. Permission, microphone and speech-service errors stop recording and explain how to continue. Dictation uses the browser's language setting.

If the microphone stays on **Starting microphone**, Studio releases the
composer after 30 seconds. Check the browser's microphone prompt and retry
using the microphone icon. If the browser's speech service is unavailable,
keyboard dictation can enter text directly into the same field.

## Speak while selecting

In slide or artifact feedback, the composer shows a compact recording bar while listening. After stopping, the transcript is editable, with small context labels retaining the original targets. Selecting another object starts a new section. Wait for **Listening** before speaking in the new section: the browser must finish the previous recognition session before starting the next one. Late final results belong to the preceding section. Interim guesses are shown separately and are not sent or silently treated as confirmed text. The browser API does not provide dependable per-word timestamps.

Use **Cmd/Ctrl/Shift-click** to add or remove objects from the selection. Slides also support modifier-click in the object picker and individual removal chips. A spoken section can target one object, a group, or the whole slide/artifact. Context includes the original version/hash and complete target snapshots. Switching slides, artifacts or revisions finishes the current session; it does not move its transcript to the new view.

Review, edit or remove dictated sections before sending. The ordinary typed message remains separate. Send uses the existing feedback API for each section, preserving its retry ID and original target/version. A failure retains unconfirmed sections; a retry does not duplicate confirmed comments. Editing a section renews its ID. Attachments belong to the ordinary message. Live releases submitted comments immediately; Batch keeps them private until **Send batch**. Dictation itself releases nothing.

Slide drafts survive browser reload and portable-project save/open; artifact drafts survive reload in local browser storage. Project saves finish active dictation before taking a snapshot. Speech that the browser has not finalized is not part of a saved draft. Discarding or cancelling a text edit rejects its late callbacks. If a field changes incompatibly while a result is pending, the result is not applied over the newer text.

## Reference and verification

The reference is [David-Sat/lux-edit's voice recorder](https://github.com/David-Sat/lux-edit/blob/3670e3b516a19988281a61ccc6f43997ce3af18b/packages/overlay/src/inspector/voice-recorder.ts). Its browser API and target-pinning approach informed this implementation. Artifact Studio uses independent context sections, bounded session shutdown, explicit error handling and its existing feedback envelopes.

Browser tests simulate `SpeechRecognition` events against the real server: interim/final results, late results across selection changes, multiple selected objects, slide/artifact navigation, reload recovery, lost feedback responses, Batch release, caret insertion, rich editor changes, unsupported browsers and denied permission. They also exercise the existing multi-object picker and inspect microphone/section layout in both themes. These tests verify application behavior; they do not establish real microphone accuracy or a provider's availability on every browser.

Hosted presenter dictation uses the updated shared runtime. Existing design/deck copies keep their copied runtime; update that runtime to use presenter dictation there. Offline and audience exports do not load voice assets.

Voice adds no MCP tools, transcription endpoints or audio/base64 fields to agent feedback. Each submitted section is an ordinary text comment with its original context. No model-token or billing saving is inferred from this feature.

The stalled-provider recovery from PR #143 was incorporated in #148. The simulated provider tests do not replace a real microphone check; the latter remains open for supported browsers and devices.
