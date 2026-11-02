"""Hand an exported file to Outlook for a new message."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_OUTLOOK = Path("/Applications/Microsoft Outlook.app")


def create_outlook_draft(path: Path) -> None:
    """Ask Outlook to open a new message with a persistent attachment copy.

    A successful return confirms macOS accepted the handoff, not that Outlook
    displayed a draft. The copy remains available while Outlook reads the file.
    """
    if sys.platform != "darwin":
        raise ValueError("Opening an Outlook draft is only available on macOS.")
    if not _OUTLOOK.is_dir():
        raise ValueError("Microsoft Outlook is not installed in /Applications.")
    if not path.is_file():
        raise ValueError("The exported attachment is not available.")

    name = re.sub(r"[^\w. -]", "_", path.name, flags=re.UNICODE).strip(". ")
    suffix = Path(name).suffix[:20]
    stem = Path(name).stem[:100].strip(". ") or "attachment"
    try:
        staged = Path(tempfile.mkdtemp(prefix="artifact-studio-outlook-")) / f"{stem}{suffix}"
        shutil.copyfile(path, staged)
        subprocess.run(
            ["/usr/bin/open", "-b", "com.microsoft.Outlook", str(staged)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except OSError as error:
        raise ValueError(f"Could not prepare or open the Outlook draft: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(
            "Outlook did not respond in time. Check Outlook before trying again; "
            "a draft may already have been created."
        ) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "Unknown Outlook error").strip()[-1200:]
        raise ValueError(f"Could not open the Outlook draft: {detail}") from error
