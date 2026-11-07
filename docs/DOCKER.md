# Run the server in Docker

Docker runs the web workspace and MCP server, including Chromium previews, slide
captures, English text checks, Git and GitHub backup tools. Your agent stays on
your computer. No model key is needed by the server.

## Start

Install Docker Engine with Compose v2, or Docker Desktop using Linux containers.
From this checkout:

```sh
docker compose up -d --build --wait
```

Open **http://127.0.0.1:8790**. Connect your agent to
**http://127.0.0.1:8790/mcp**, using the same [agent setup](../README.md#connect-your-agent)
as a local server. Stop another Studio server before using the same port. To
choose a different port, prefix Compose commands with `ARTIFACT_STUDIO_PORT=8791`.

```sh
docker compose logs --tail=100 studio
docker compose down
```

The named `studio-data` volume survives `down`, container replacement and image
updates. Start with `up -d --wait` again. **`down --volumes` deletes that workspace.**
This starts an empty workspace; it does not import an existing local collection.
Use Studio's **Save workspace** / **Restore workspace** to move one between installs.
Restoring creates a new folder without switching the running workspace. After
restoring to `/data/recovered`, add these settings to `compose.override.yaml`,
then run `docker compose up -d --wait`:

```yaml
services:
  studio:
    environment:
      ARTIFACT_STUDIO_COLLECTION: /data/recovered
      ARTIFACT_STUDIO_DATABASE: /data/recovered/.artifact-studio/review.sqlite3
```

Use the actual destination shown by Restore workspace. See
[workspace recovery](WORKSPACE-RECOVERY.md) for what a backup retains.

The image runs as UID/GID 1000, publishes only to host loopback, and enables the
Chromium sandbox through the bundled seccomp profile. Use the provided Compose
settings: a plain `docker run` lacks the required browser sandbox settings.
The Docker host must support unprivileged user namespaces. If previews fail, ask
the agent for `get_capture_status(probe_renderer=True)`; `/api/health` checks the
server, not Chromium. Do not disable the browser sandbox to work around host limits.

This is a local, single-user server. It has no login for public hosting; its host
and MCP origin checks accept `127.0.0.1` and `localhost`. Other containers cannot
connect using the Compose service name. The Mac menu-bar controller manages a
host checkout, not this container; use Compose to start, stop and update it.

## Persistent files and imports

| Container path | Contents |
| --- | --- |
| `/data/collection` | Presentations, design packs and authored artifact files. |
| `/data/state` | Review database, feedback attachments, history, previews, library caches and backup checkpoints. |
| `/data/config/gh` | GitHub CLI login, only if you explicitly sign in. |
| `/data/imports` | Files staged for agent imports. |

Keep **all of `/data`** together and run one server per volume. If using a host
directory instead of the named volume, it must be writable by UID/GID 1000.
Keep the container collection/database paths stable when changing images.

MCP paths are paths **inside the container**. An agent on the host can read text
through `get_slide`, `get_design_file` and `read_artifact_files`; use returned
HTTP download URLs for other files (prefix relative URLs with the Studio URL).
Captures return image pixels directly through MCP.

To import a local file, stream it as the container's runtime user:

```sh
docker compose exec -T studio sh -c 'cat > /data/imports/example.md' < ./example.md
```

For an authored folder:

```sh
docker compose exec -T studio mkdir -p /data/imports/my-artifact
tar -C ./my-artifact -cf - . | docker compose exec -T studio tar -xf - -C /data/imports/my-artifact
```

Then use `/data/imports/my-artifact` as `source_path` with `register_artifact`.
For the single-file example, use `/data/imports/example.md`. Unlike `compose cp`,
streaming does not retain a host UID that might make a private file unreadable
inside the container. For design or presentation asset imports, write into the
tool's returned `import_directory` instead. This retains the existing allowed-source checks.

For frequent imports, create a `compose.override.yaml` with a dedicated shared
folder. Only mount files you want the server to read:

```yaml
services:
  studio:
    volumes:
      - ./imports:/imports:ro
```

Create `./imports` before starting; an artifact saved there as `example/index.html`
can be registered with `source_path="/imports/example"`. This read-only mount is
for importing artifacts; design/slide asset staging directories remain under `/data`.

## Live feedback and GitHub backups

Configure Codex/Claude lifecycle hooks **on the host**, from a matching checkout
with `uv sync --locked`. Follow [Live review](LIVE-REVIEW.md) using the published
localhost URL. Hook configuration inside the image would point at container-only
paths. Plain MCP can read feedback; automatic continuation still requires loaded
host hooks and a running agent session.

Git and `gh` are installed; GitHub access is opt-in. To authenticate this container
using the device flow (choose HTTPS):

```sh
docker compose exec studio gh auth login --hostname github.com --git-protocol https --web
```

The login is stored in the private `/data/config/gh` directory. The image provides
the `gh` GitHub HTTPS credential helper, so both API access and Git sync can use
that login after recreation. Your host login is not copied into the image. If instead supplying
`GH_TOKEN` through your own Compose override, supply it on every start and keep
the token out of source control. Tokens and credentials are not part of Studio's
portable workspace exports. Configure repository access and schedules in Studio
as described in [full backups](REPOSITORY-SYNC.md#scheduled-full-workspace-backups).

## Update and build checks

Check out the desired release or nightly tag, then rebuild and recreate:

```sh
docker compose up -d --build --wait
```

The in-app source-checkout updater is disabled in the image. Images are built
locally; this repository does not yet publish them to a registry. The checked-out
source determines the version. Standard Docker builders support both Linux
amd64 and arm64 base images; the Docker CI journey exercises amd64.

The Docker CI workflow builds the locked image and exercises HTTP/MCP, real
sandboxed captures, text checks, imports and persistence using a disposable volume.
Run it locally after building:

```sh
uv sync --locked
ARTIFACT_STUDIO_DOCKER_TESTS=1 uv run --no-sync pytest tests/integration/test_docker.py
```

For an HTTPS inspection proxy, pass a combined CA bundle as a temporary BuildKit
secret: `docker build --secret id=proxy_ca,src=/path/to/combined-ca.pem -t artifact-studio:local .`.
The certificate is not stored in the image. Runtime network clients need their
own read-only CA mount and trust configuration if the same proxy is required.
