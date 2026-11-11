"""Render one bounded hosted PDF in a separate process."""

import resource
import sys
from pathlib import Path

from .sharing import write_pdf_document


def main(document: Path, output: Path, limit: int) -> None:
    """Apply child resource limits before opening the print document."""
    resource.setrlimit(resource.RLIMIT_CPU, (40, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    write_pdf_document(document.read_text(encoding="utf-8"), output, limit)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
