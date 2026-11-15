"""Compare feature import cost and unchanged browser payloads across two checkouts.

Run with the candidate's Python environment and --baseline PATH. This measures
fresh-process import time, not end-user launch latency or model token billing.
"""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import cast


def sample(root: Path, modules: list[str]) -> float:
    code = (
        "import importlib,json,time; started=time.perf_counter(); "
        f"[importlib.import_module(name) for name in {modules!r}]; "
        "print(json.dumps((time.perf_counter()-started)*1000))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True
    )
    return float(result.stdout)


def revision(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for folder, suffixes in (("artifact_studio", {".py"}), ("web", {".js", ".html"})):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                digest.update(path.relative_to(root).as_posix().encode() + b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pairs", type=int, default=5)
    arguments = parser.parse_args()
    baseline = cast(Path, arguments.baseline).resolve()
    candidate = cast(Path, arguments.candidate).resolve()
    pairs = cast(int, arguments.pairs)
    if not 1 <= pairs <= 20:
        parser.error("--pairs must be between 1 and 20")
    modules = {
        "baseline": ["artifact_studio.design_authoring", "artifact_studio.element_alternatives"],
        "candidate": ["artifact_studio.designs.authoring", "artifact_studio.designs.elements"],
    }
    roots = {"baseline": baseline, "candidate": candidate}
    # Warm the filesystem once; every measured import still uses a fresh process.
    for label, root in roots.items():
        _ = sample(root, modules[label])
    samples: dict[str, list[float]] = {label: [] for label in roots}
    for index in range(pairs):
        for label in ["baseline", "candidate"] if index % 2 == 0 else ["candidate", "baseline"]:
            samples[label].append(sample(roots[label], modules[label]))
    assets: list[dict[str, object]] = []
    for old, new in (
        ("presentation-launcher.js", "launcher.js"),
        ("presentation-warmup.js", "warmup.js"),
        ("presentations.js", "library-actions.js"),
    ):
        before = (baseline / "web" / old).read_bytes()
        after = (candidate / "web/presentation" / new).read_bytes()
        if before != after:
            raise ValueError(f"Browser payload changed: {old}")
        assets.append(
            {
                "historical": old,
                "canonical": new,
                "bytes": len(after),
                "sha256": hashlib.sha256(after).hexdigest(),
            }
        )
    if (baseline / "agent/tools.json").read_bytes() != (
        candidate / "agent/tools.json"
    ).read_bytes():
        raise ValueError("The tool catalogue changed")
    print(
        json.dumps(
            {
                "method": "one filesystem warmup; alternating pairs; fresh process per sample",
                "python": sys.version.split()[0],
                "pairs": pairs,
                "revisions": {label: revision(root) for label, root in roots.items()},
                "source_fingerprints": {
                    label: source_fingerprint(root) for label, root in roots.items()
                },
                "import_ms": {
                    label: {"samples": values, "median": statistics.median(values)}
                    for label, values in samples.items()
                },
                "browser_assets": assets,
                "browser_requests_before": len(assets),
                "browser_requests_after": len(assets),
                "catalogue_identical": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
