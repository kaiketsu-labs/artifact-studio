"""Deterministic browser speech events without microphone access or external providers."""

from playwright.sync_api import Page

SPEECH_MOCK = r"""
(() => {
  const sessions = [];
  class Recognition {
    constructor() {
      this.results = [];
      this.starts = 0;
      this.stops = 0;
      this.aborts = 0;
      sessions.push(this);
    }
    start() { this.starts++; this.onstart?.({type: 'start'}); }
    stop() { this.stops++; }
    abort() { this.aborts++; }
    result(text, final = true, index = null) {
      if (index === null) index = this.results.length && !this.results.at(-1).isFinal
        ? this.results.length - 1 : this.results.length;
      const result = [{transcript: text, confidence: 1}];
      result.isFinal = final;
      this.results[index] = result;
      this.onresult?.({resultIndex: index, results: this.results});
    }
    end() { this.onend?.({type: 'end'}); }
    fail(error) { this.onerror?.({error}); }
  }
  Object.defineProperty(window, 'SpeechRecognition', {value: Recognition, configurable: true});
  Object.defineProperty(window, 'webkitSpeechRecognition', {value: undefined, configurable: true});
  window.__speech = {sessions};
})();
"""


def install_voice(page: Page, *, supported: bool = True) -> None:
    """Install before navigation; stop deliberately waits for an explicit end or timeout."""
    if supported:
        page.add_init_script(SPEECH_MOCK)
    else:
        page.add_init_script(
            "Object.defineProperty(window,'SpeechRecognition',{value:undefined,configurable:true});"
            "Object.defineProperty(window,'webkitSpeechRecognition',"
            "{value:undefined,configurable:true});"
        )


def voice_result(
    page: Page, text: str, *, final: bool = True, session: int = -1, index: int | None = None
) -> None:
    """Emit a genuine-shaped cumulative result event, including replaced interim text."""
    page.evaluate(
        "value => window.__speech.sessions.at(value.session)"
        ".result(value.text,value.final,value.index)",
        {"text": text, "final": final, "session": session, "index": index},
    )


def voice_end(page: Page, *, session: int = -1) -> None:
    """Release a stopped session after any intentionally delayed final results."""
    page.evaluate("index => window.__speech.sessions.at(index).end()", session)


def voice_error(page: Page, error: str, *, session: int = -1) -> None:
    """Inject a browser/provider failure without relying on OS microphone permissions."""
    page.evaluate(
        "value => window.__speech.sessions.at(value.session).fail(value.error)",
        {"error": error, "session": session},
    )


def voice_sessions(page: Page) -> int:
    """Count recognizers to catch unintended restarts after errors or navigation."""
    return int(str(page.evaluate("window.__speech.sessions.length")))
