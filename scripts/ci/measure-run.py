"""Report first-attempt feedback and allocated runner seconds from Actions."""

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime


def api(endpoint: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def elapsed(start: str, end: str) -> int:
    return int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def measure(repo: str, run_id: int) -> dict:
    endpoint = f"repos/{repo}/actions/runs/{run_id}/attempts/1"
    run = api(endpoint)
    if run["status"] != "completed":
        raise ValueError(f"Run {run_id} is still {run['status']}")
    by_label = Counter()
    browsers = {}
    feedback = None
    page = 1
    while True:
        jobs = api(f"{endpoint}/jobs?per_page=100&page={page}")["jobs"]
        for job in jobs:
            if job["conclusion"] == "skipped" or not job["runner_id"]:
                continue
            seconds = elapsed(job["started_at"], job["completed_at"])
            by_label[", ".join(job["labels"])] += seconds
            if job["name"].startswith("Playwright user journeys and rendering"):
                browsers[job["name"]] = seconds
            if job["name"] == "Dependency safety":
                feedback = elapsed(run["created_at"], job["completed_at"])
        if len(jobs) < 100:
            break
        page += 1
    return {
        "run": run_id,
        "sha": run["head_sha"],
        "event": run["event"],
        "conclusion": run["conclusion"],
        "seconds_to_gate": feedback,
        "runner_seconds_by_label": by_label,
        "browser_runner_seconds": sum(browsers.values()),
        "browser_jobs_seconds": browsers,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_ids", type=int, nargs="+")
    parser.add_argument("--repo", default="kaiketsu-labs/artifact-studio")
    args = parser.parse_args()
    for run_id in args.run_ids:
        print(json.dumps(measure(args.repo, run_id)))
