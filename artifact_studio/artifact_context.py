"""Scoped, inspectable guidance receipts for the shared artifact workspace."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .agent_io import guide_fields, rule_fields
from .artifacts import ArtifactStore
from .design_library import DesignLibrary
from .storage import Store, digest

PROJECT = Path(__file__).resolve().parent.parent


def artifact_context(
    store: Store,
    *,
    artifact_id: str = "",
    project_id: str = "",
    known_rules_hash: str = "",
    known_guide_hash: str = "",
    record_delivery: bool = False,
    design_id: str = "",
    include_discovery: bool = True,
) -> dict[str, object]:
    """Resolve applicable guidance without loading unrelated artifact source bodies."""
    artifacts = ArtifactStore(store)
    artifact = artifacts.get_artifact(artifact_id) if artifact_id else None
    if artifact:
        if project_id and project_id != artifact.project_id:
            raise ValueError("Artifact and project do not match.")
        project_id = artifact.project_id
    project = next((item for item in artifacts.list_projects() if item.id == project_id), None)
    if project_id and project is None:
        raise FileNotFoundError("Project not found.")
    rules = rule_fields(
        store.authoring_instructions(), store.learned_preferences(), known_rules_hash
    )
    guide_path = PROJECT / "agent/guides/artifacts.md"
    guide = guide_fields(guide_path.read_text(), known_guide_hash)
    instruction_path = store.root / "INSTRUCTIONS.md"
    instruction_source = str(instruction_path)
    if not instruction_path.exists():
        with store.connect() as db:
            saved = db.execute(
                "SELECT value FROM workspace_settings WHERE key='authoring_instructions'"
            ).fetchone()
        instruction_source = (
            f"{store.database}#workspace_settings/authoring_instructions"
            if saved
            else str(PROJECT / "docs/AUTHORING-INSTRUCTIONS.md")
        )
    state: Literal["returned", "available"] = "returned" if record_delivery else "available"
    rule_state = "omitted because retained" if not rules["rules_included"] else state
    sources: list[dict[str, str]] = [
        {
            "title": "Workspace rules",
            "scope": "workspace",
            "path": instruction_source,
            "hash": digest(store.authoring_instructions()),
            "delivery": rule_state,
        },
        {
            "title": "Learned preferences",
            "scope": "workspace · secondary",
            "path": str(store.root / "LEARNED-PREFERENCES.md"),
            "hash": digest(store.learned_preferences()),
            "delivery": rule_state,
        },
        {
            "title": "Artifact guide",
            "scope": "tool",
            "path": str(guide_path),
            "hash": str(guide["guide_hash"]),
            "delivery": state if guide["guide_included"] else "omitted because retained",
        },
    ]
    if project:
        sources.append(
            {
                "title": "Project brief",
                "scope": project.id,
                "path": str(Path(project.source_path) / "README.md") if project.source_path else "",
                "hash": project.context_hash,
                "delivery": state,
            }
        )
    if artifact:
        sources.append(
            {
                "title": "Artifact context",
                "scope": artifact.id,
                "path": str(Path(artifact.source_path) / "README.md")
                if artifact.legacy_deck_id
                else str(Path(artifact.source_path).parent / "artifact.json"),
                "hash": artifact.context_hash,
                "delivery": state,
            }
        )
    selected_design = DesignLibrary(store).get(design_id) if design_id else None
    if selected_design:
        sources.extend(
            {
                "title": file.name,
                "scope": "design · " + selected_design.id,
                "path": file.local_path,
                "hash": file.sha256,
                "delivery": "pointer returned" if record_delivery else "available",
            }
            for file in selected_design.files
        )
    result: dict[str, object] = {
        "discovery_included": include_discovery,
        "available_designs": [item.model_dump() for item in DesignLibrary(store).list()]
        if include_discovery
        else [],
        "design_context": selected_design.model_dump() if selected_design else None,
        **rules,
        **guide,
        "project": project.model_dump() if project else None,
        "artifact": {
            **artifact.model_dump(exclude={"files"}),
            "file_count": len(artifact.files),
            "file_inventory_tool": "get_artifact",
        }
        if artifact
        else None,
        "sources": sources,
        "related_artifacts": [
            {"id": item.id, "title": item.title, "kind": item.kind}
            for item in artifacts.list_artifacts(project_id)
            if item.id != artifact_id
        ]
        if project_id
        else [],
        "capabilities": {
            "free_composition": True,
            "custom_html_css_javascript": True,
            "design_required": False,
            "external_network": False,
            "generic_artifact_live_hooks": False,
            "legacy_presentation_tools": True,
        },
        "context_policy": "Workspace rules are primary; project and artifact context are scoped. "
        "Related artifact updates require a suggestion first. "
        "Source pointers are not read receipts.",
    }
    with store.connect() as db:
        _ = db.execute(
            "CREATE TABLE IF NOT EXISTS artifact_context_receipts "
            "(scope TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
        )
        scope = artifact_id or project_id or "workspace"
        if record_delivery:
            receipt = {
                "at": datetime.now(UTC).isoformat(),
                "sources": sources,
                "artifact_id": artifact_id,
                "project_id": project_id,
            }
            _ = db.execute(
                "INSERT INTO artifact_context_receipts VALUES (?,?) "
                "ON CONFLICT(scope) DO UPDATE SET receipt=excluded.receipt",
                (scope, json.dumps(receipt)),
            )
        row = db.execute(
            "SELECT receipt FROM artifact_context_receipts WHERE scope=?", (scope,)
        ).fetchone()
        result["receipt"] = json.loads(str(row[0])) if row else None
    return result
