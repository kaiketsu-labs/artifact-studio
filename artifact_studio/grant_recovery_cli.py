"""Trusted operator commands for archives with optional human sharing grants."""

import argparse
from pathlib import Path

from .authentication import Principal
from .deployment import DeploymentConfig
from .grant_recovery import identity_context, read_staged_grants, snapshot_grants
from .multiplayer_access import WORKSPACE_ID, Sharing
from .storage import Store
from .workspace_recovery import WorkspaceRecovery


def main() -> int:
    """Export or restore a private archive without exposing server paths to HTTP."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export")
    export.add_argument("--collection", type=Path, required=True)
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--sharing-root", type=Path, required=True)
    export.add_argument("--owner-email", required=True)
    export.add_argument("--owner-subject", required=True)
    export.add_argument("--workspace", default="default")
    export.add_argument("--archive", type=Path, required=True)
    restore = actions.add_parser("restore")
    restore.add_argument("--collection", type=Path, required=True)
    restore.add_argument("--database", type=Path, required=True)
    restore.add_argument("--archive", type=Path, required=True)
    activate = actions.add_parser("activate")
    activate.add_argument(
        "--collection", type=Path, required=True, help="active default collection"
    )
    activate.add_argument("--database", type=Path, required=True, help="active default database")
    activate.add_argument("--sharing-root", type=Path, required=True)
    activate.add_argument("--owner-email", required=True)
    activate.add_argument("--owner-subject", required=True)
    activate.add_argument("--workspace", required=True)
    activate.add_argument("--title", required=True)
    args = parser.parse_args()
    store = Store(args.collection, args.database)
    recovery = WorkspaceRecovery(store)
    if args.action == "activate":
        if (
            not WORKSPACE_ID.fullmatch(args.workspace)
            or not args.title.strip()
            or len(args.title) > 160
        ):
            raise ValueError("Use the original non-default workspace ID and a title.")
        config = DeploymentConfig.from_env()
        hub = Sharing(args.sharing_root, store, args.owner_email)
        path = hub.root / "workspaces" / args.workspace
        restored_store = Store(path / "collection", path / "state" / "review.sqlite3")
        staged = read_staged_grants(restored_store)
        with hub.locked():
            owner = hub.human(Principal(args.owner_subject, args.owner_email))
            snapshot = staged.snapshot
            if (
                snapshot.workspace != args.workspace
                or snapshot.owner != owner.person
                or snapshot.owner_email != args.owner_email.lower()
                or (snapshot.issuer, snapshot.audience) != identity_context(config)
            ):
                raise ValueError(
                    "Staged grants do not match this owner, workspace and identity context."
                )
            with hub.connect() as db:
                if any(
                    db.execute(
                        f"SELECT 1 FROM {table} WHERE workspace=? LIMIT 1", (args.workspace,)
                    ).fetchone()
                    for table in ("grants", "invitations", "agents")
                ):
                    raise ValueError("This workspace ID already has sharing state.")
                if db.execute("SELECT 1 FROM workspaces WHERE id=?", (args.workspace,)).fetchone():
                    raise ValueError("This workspace ID is already registered.")
                db.execute(
                    "INSERT INTO workspaces VALUES (?,?,?)",
                    (args.workspace, args.title.strip(), owner.person),
                )
        print(f"Registered workspace {args.workspace}. Review its staged human grants in Studio.")
    elif args.action == "export":
        config = DeploymentConfig.from_env()
        hub = Sharing(args.sharing_root, store, args.owner_email)
        with hub.locked():
            owner = hub.human(Principal(args.owner_subject, args.owner_email))
            registered = hub.store(args.workspace)
            if registered.root != store.root or registered.database != store.database:
                raise ValueError("Collection does not match the selected workspace.")
            snapshot = snapshot_grants(hub, owner, args.workspace, config)
            summary = recovery.export_archive(args.archive, human_grants=snapshot)
        print(f"Saved {summary.path} (SHA-256 {summary.sha256}).")
    else:
        preview = recovery.preview(args.archive, mode="archive")
        if not preview.can_restore:
            raise ValueError(" ".join(preview.conflicts))
        recovery.restore(args.archive, preview.content_hash, mode="archive")
        print(f"Restored files into {store.root}. Activate this workspace to review human grants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
