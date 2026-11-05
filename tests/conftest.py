"""Keep default application state and diagnostics separate in parallel test workers."""

import os
import tempfile
from pathlib import Path


def pytest_configure() -> None:
    """Isolate worker defaults before test collection imports the application."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    run = os.environ.get("PYTEST_XDIST_TESTRUNUID")
    if not worker or not run:
        return
    namespace = Path(run) / worker
    fallback = Path(tempfile.gettempdir()) / "artifact-studio-tests"
    collection = Path(os.environ.get("ARTIFACT_STUDIO_COLLECTION", str(fallback / "presentations")))
    database = Path(os.environ.get("ARTIFACT_STUDIO_DATABASE", str(fallback / "review.sqlite3")))
    os.environ["ARTIFACT_STUDIO_COLLECTION"] = str(collection / namespace)
    os.environ["ARTIFACT_STUDIO_DATABASE"] = str(database.parent / namespace / database.name)
    artifacts = os.environ.get("ARTIFACT_STUDIO_ARTIFACTS")
    if artifacts:
        os.environ["ARTIFACT_STUDIO_ARTIFACTS"] = str(Path(artifacts) / namespace)
