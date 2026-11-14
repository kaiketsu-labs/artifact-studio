# Recover human sharing access

An operator can include human sharing grants in a private workspace archive. These
records contain email addresses, identity hashes, scopes and roles. They do not
contain invitation links, agent tokens, or identity-provider credentials. Treat the
archive as private. This is separate from the scheduled full-backup setting; existing
backups do not automatically include sharing grants.

Grant recovery currently supports Cloudflare Access. It binds the backup to the exact
Access team, application audience, workspace ID, and owner subject and email. An
archive from a different identity context cannot restore access. Do not change or
reuse identity contexts to bypass that check.

On the server, with `ARTIFACT_STUDIO_MODE=hosted` and
`ARTIFACT_STUDIO_HOSTED_CONFIG` set to the active private configuration, a trusted
operator can run:

```sh
uv run python -m artifact_studio.grant_recovery_cli export \
  --collection /private/collection --database /private/state/review.sqlite3 \
  --sharing-root /private/multiplayer --owner-email owner@example.test \
  --owner-subject 'verified-owner-subject' --workspace default \
  --archive /private/backups/workspace.zip
```

Use the actual verified owner subject from the identity provider, the active workspace
ID, and the server's private collection, database, and multiplayer registry paths. The
command checks ownership and that the selected store matches the workspace. It never
replaces an existing archive. It includes grants from that workspace only.

Restore into an empty collection and fresh review database, with the server stopped:

```sh
uv run python -m artifact_studio.grant_recovery_cli restore \
  --archive /private/backups/workspace.zip --collection /private/restored \
  --database /private/restored-state/review.sqlite3
```

For a non-default workspace, restore its collection and database into the fixed paths
`<sharing-root>/workspaces/<workspace-id>/collection` and
`<sharing-root>/workspaces/<workspace-id>/state/review.sqlite3`. With the server
stopped, register that original ID without changing any other workspace:

```sh
uv run python -m artifact_studio.grant_recovery_cli activate \
  --collection /private/default-collection --database /private/default-state/review.sqlite3 \
  --sharing-root /private/multiplayer --owner-email owner@example.test \
  --owner-subject 'verified-owner-subject' --workspace ORIGINAL_WORKSPACE_ID \
  --title 'Recovered workspace'
```

The default collection and database arguments identify the server's default workspace;
the command reads the restored non-default workspace only from its fixed paths. It
requires an unregistered ID with no existing sharing state and checks its staged owner
and identity context before registration.

Start the server against the restored collection and review database with a fresh
sharing registry for that workspace, the same workspace ID, owner, and Cloudflare
Access configuration. Sign in as the original owner, open workspace sharing, and
review the staged identities and their roles and scopes. Select the confirmation and
restore access only if every entry is expected. Existing sharing, invitations, or
agent connections in the destination prevent applying the staged grants. Other
workspaces in the registry are not changed. Reconnect agents and create new invitation
links separately.

Restoring files never applies permissions automatically. Unknown resources, conflicting
identities, a changed preview, or a different owner or identity context prevent grant
restoration. The archive hash in the review identifies the source archive; it does not
prove an archive is authentic. Review the source and every listed identity.
