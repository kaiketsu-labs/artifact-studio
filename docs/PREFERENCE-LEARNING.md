# Learn from presentation feedback

The bundled [`slide-studio-learn`](../.agents/skills/slide-studio-learn/SKILL.md) skill reviews human presentation conversations and released Slide Studio feedback. It updates preferences with evidence, keeps each client's context separate, and preserves explicit workspace rules.

| Scope | Local file |
| --- | --- |
| Explicit rules for every presentation, maintained by the human | Collection `INSTRUCTIONS.md` |
| Learned general preferences, secondary to explicit rules | Collection `LEARNED-PREFERENCES.md` |
| Preference provenance and history, loaded only for learning/review | Collection `LEARNED-PREFERENCES-EVIDENCE.md` |
| Reusable treatment for one design | That design's `PREFERENCES.md` |
| Content, client, audience and one-off choices | That deck's `README.md` |
| Reviewed source IDs, pending decisions and checkpoints | Application `.state/learning-cursors.json` |

“For future presentations, use shorter headlines” can become a general preference. “Use this client's terminology” stays scoped. A repeated request in one deck is still one-deck evidence. Application UI requests do not become rules for audience slides.

Keep agent-facing guidance short and actionable, with stable preference IDs. Store rationale and source IDs in the separate evidence file; MCP does not include that file in ordinary authoring reads. Preserve conditions and exceptions rather than truncating rules. Merge duplicates instead of accumulating explanations after every review.

## Invoke the skill

From a Codex project opened in the Slide Studio checkout, ask:

> Use $slide-studio-learn to review my recent presentation transcripts and released feedback, then update the appropriate preferences.

The skill is checked into `.agents/skills`, so a fresh clone includes it. If your chat is working in a different folder, give it the absolute path to this checkout's `.agents/skills/slide-studio-learn/SKILL.md`. The agent can read that file directly. Other skill-capable harnesses can load the same folder through their supported skill mechanism; their chat-history and scheduling tools may differ.

In Codex desktop, the workflow uses chat listing/reading tools to find relevant human messages, excluding tool outputs. Other harnesses need an authorized transcript interface or supplied exports. MCP supplies released slide feedback and workspace rules, not access to every agent's history. Unavailable sources are reported rather than silently marked reviewed.

## Schedule a weekly run

Ask your harness to run the skill weekly with a day, time and timezone. For example:

> Every Tuesday at 10:00 Europe/Berlin, read and follow the slide-studio-learn skill in this checkout. Review new presentation-related transcripts and released feedback. Keep changes local, preserve explicit workspace rules, and notify me only for meaningful preference updates, a new failure or a decision I need to make.

Update an existing learning schedule rather than adding a duplicate. Keep the saved prompt linked to the skill's file so future skill improvements apply on the next run. Installing Slide Studio does not create a schedule; the host owns scheduling and notifications. For local runs, the computer and host app must be available, and Slide Studio must be running for MCP feedback reads. [Codex scheduling documentation](https://learn.chatgpt.com/docs/automations).

The first review looks back seven days; subsequent runs use per-source checkpoints. A source overlap catches late availability without counting the same comment twice. A failed source or write stays eligible for retry. Explicit rules are never rewritten by automatic learning. When a proposed preference conflicts with those rules, the run records the question for the human.
