"""Prepare portable custom documents for isolated slide execution."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .hosted_policy import deck_asset_path
from .projects import AssetEmbedder, EmbeddedMarkup
from .storage import Store

MAX_DOCUMENT = 2_000_000


class DocumentParts(list[str]):
    """Bound expanded resource allocations before joining an interactive document."""

    def __init__(self) -> None:
        """Start with an empty source-size budget."""
        super().__init__()
        self.size = 0

    def append(self, value: str) -> None:
        """Reject oversized expansion before retaining another copied token."""
        self.size += len(value)
        if self.size > MAX_DOCUMENT:
            raise ValueError("Embedded interactive HTML exceeds 2 MB. Resize assets first.")
        super().append(value)


class InteractiveMarkup(EmbeddedMarkup):
    """Inline local scripts and styles before ordinary image and font substitution."""

    def __init__(
        self, embedder: AssetEmbedder, directory: Path | None = None, depth: int = 0
    ) -> None:
        """Retain a single self-contained document token stream."""
        super().__init__(embedder, directory)
        self.depth = depth
        self.external_script = False
        self.parts = DocumentParts()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Embed local script and stylesheet files while retaining unrestricted HTML."""
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            path = self.embedder.local_path(values["src"] or "", self.directory)
            source = re.sub(r"</script", r"<\\/script", path.read_text(), flags=re.I)
            kind = values.get("type", "text/javascript")
            if kind not in {"text/javascript", "application/javascript", "module"}:
                raise ValueError("Use an inline script for non-JavaScript script data.")
            self.parts.append(f'<script type="{kind}">' + source)
            self.external_script = True
            return
        if tag == "link" and (values.get("rel") or "").lower() == "stylesheet":
            path = self.embedder.local_path(values.get("href") or "", self.directory)
            css = self.embedder.css(path.read_text(), path.parent).replace("<", r"\3c ")
            self.parts.append("<style>" + css + "</style>")
            return
        super().handle_starttag(tag, attrs)

    def embedded_document(self, html: str) -> str:
        """Prepare nested srcdoc with the same resource boundary and expansion budget."""
        if self.depth >= 6:
            raise ValueError("Keep embedded iframe documents at or below 6 levels.")
        parser = InteractiveMarkup(self.embedder, self.directory, self.depth + 1)
        parser.feed(html)
        parser.close()
        return "".join(parser.parts)

    def handle_data(self, data: str) -> None:
        """Ignore original fallback bodies of replaced external scripts."""
        if not self.external_script:
            super().handle_data(data)

    def handle_endtag(self, tag: str) -> None:
        """Finish an embedded script and resume ordinary token handling."""
        if tag == "script" and self.external_script:
            self.external_script = False
            self.parts.append("</script>")
            return
        super().handle_endtag(tag)


class InteractiveAssets(AssetEmbedder):
    """Bound local resource size before producing an MCP-returnable document."""

    def __init__(self, folder: Path, *, scoped: bool = False) -> None:
        """Track repeated data-URL expansion as well as distinct local files."""
        super().__init__(folder)
        self.expanded = 0
        self.scoped = scoped
        self.css_limit = MAX_DOCUMENT

    def asset(self, url: str, directory: Path | None = None) -> str:
        """Stop repeated resource expansion before constructing an oversized string."""
        result = super().asset(url, directory)
        self.expanded += len(result)
        if self.expanded > MAX_DOCUMENT:
            raise ValueError("Embedded interactive HTML exceeds 2 MB. Resize assets first.")
        return result

    def local_path(self, url: str, directory: Path | None = None) -> Path:
        """Keep reads inside the deck and avoid unexpectedly large resource allocations."""
        if self.scoped:
            parsed = urlsplit(url)
            relative = (directory or self.folder) / unquote(parsed.path)
            if parsed.scheme or parsed.netloc or not relative.is_relative_to(self.folder):
                raise ValueError("Use a shared deck asset.")
            _ = deck_asset_path(
                self.folder, relative.relative_to(self.folder).as_posix(), embedded=True
            )
        path = super().local_path(url, directory)
        if path.stat().st_size > MAX_DOCUMENT:
            raise ValueError("Resize interactive dependencies to fit the 2 MB document limit.")
        return path


def prepare_document(
    store: Store, deck_id: str, html: str, *, include_theme: bool = True, scoped: bool = False
) -> str:
    """Inline deck-local dependencies into a custom document without executing its code."""
    if len(html) > MAX_DOCUMENT:
        raise ValueError("Interactive documents must fit the 2 MB source limit.")
    if scoped and (store.root / deck_id).is_symlink():
        raise ValueError("Shared presentations require an unlinked source file.")
    embedder = InteractiveAssets(store.folder(deck_id), scoped=scoped)
    parser = InteractiveMarkup(embedder)
    parser.feed(html)
    parser.close()
    theme = ""
    if include_theme:
        path = embedder.local_path("theme.css")
        theme = "<style>" + embedder.css(path.read_text()).replace("<", r"\3c ") + "</style>"
    document = theme + "".join(parser.parts)
    if len(document) > MAX_DOCUMENT:
        raise ValueError("Embedded interactive HTML exceeds 2 MB. Resize assets first.")
    return document
