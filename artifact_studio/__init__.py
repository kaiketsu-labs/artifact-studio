"""Local presentation review and stateless agent tools."""

import tomllib
from pathlib import Path

with (Path(__file__).resolve().parent.parent / "pyproject.toml").open("rb") as project_file:
    __version__: str = tomllib.load(project_file)["project"]["version"]
