# Mac menu-bar app

Artifact Studio can live in the menu bar, start at login and run without a Terminal window. The native controller starts your existing Python server; it keeps the same collection, database and MCP URL. No npm or Node installation is needed.

## Install from this checkout

Install the source dependencies using the [quick start](../README.md#quick-start) first. Building the controller requires macOS 13 or later and Apple's Command Line Tools (`xcode-select --install`). Wait for the tools installation to finish, then run from the Artifact Studio checkout:

```sh
uv run --no-sync python scripts/install_macos.py --launch-at-login
```

If a Terminal server is running, stop it with **Ctrl+C** before opening the app. Open `~/Applications/Artifact Studio.app` in Finder. An artifact icon appears beside the clock. Launch at Login is enabled on the first run when requested by the installer; its checkmark reports the macOS registration. If macOS requires approval, **Allow in System Settings…** opens Login Items. You can turn automatic launch off from the same menu.

| Control | Behavior |
| --- | --- |
| Pixel status square | Filled: responding. Small: starting/stopping. Outline: stopped or failed; open the menu for the exact status. The icon follows the menu bar's light/dark appearance. |
| Open Artifact Studio | Opens the workspace in your default browser. |
| Start / Stop / Restart | Controls the server started by this menu-bar app. Stop waits for a graceful shutdown. |
| Update… | Checks GitHub main, installs an available update and rebuilds the controller. A running server requires confirmation before it stops and restarts. A stopped server stays stopped. |
| Settings → Launch at Login | Toggles automatic startup; a checkmark means enabled. If macOS needs approval, the same submenu opens Login Items. |
| Settings → GitHub Backup | Repository delivery status, last successful backup, Automatic Backup, Sync Now/Retry, and Open Backup Settings. Uses the server’s shared settings. |
| Show Logs | Reveals the local server log in Finder. |
| Quit Artifact Studio | Gracefully stops its server and closes the menu-bar app. Launch at Login remains configured. |

The controller checks `/api/health` every two seconds, without reading decks or private notes. Backup settings/status load when you open the menu; the controller does not continuously reread artifact content. If another Artifact Studio already uses the configured port, it displays **Running · started elsewhere** and disables Stop/Restart. Stop that instance from its original Terminal or controller, then use **Start** here. It never kills an unrelated process using the same port.

Closing a browser tab does not stop the server. Stopping or restarting the server interrupts active agent connections; unsent attachments stay in the tab, while submitted feedback and saved decks remain on disk. A crashed server shows a failed state; Start retries deliberately rather than looping indefinitely.

## Existing workspaces and updates

The defaults are `~/Documents/Artifacts`, this checkout's `.state/review.sqlite3`, and port 8790. Override them at installation with `--collection`, `--database` and `--port`. App configuration lives in `Contents/Resources/configuration.json`; logs live in `~/Library/Logs/labs.kaiketsu.artifact-studio/server.log`.

Keep this checkout and its `.venv` in place. Choose **Update…** in the Mac menu to update the server, browser interface and native controller together. It fetches `origin/main` and accepts a clean fast-forward on `main`; commit or move unfinished source edits first. It does not auto-commit, discard edits, reconcile divergent branches or change your collection/database paths. Active server and agent connections are interrupted only after you confirm **Update and Restart**. Refresh browser tabs and resume agent review afterward.

The updater preserves the previous environment while installing locked dependencies and Chromium, and builds the new controller before replacing the app. Installation failures restore the previous source and environment; a concurrent source edit is preserved and requires manual recovery instead of being reset. **Show Logs** reveals server and update logs. Updating a server started elsewhere is refused; stop it in its original controller first.

The controller relaunches after a successful update and restores the prior running/stopped state. It remains a local source installation requiring `uv` and Apple's Command Line Tools, rather than a self-contained, notarized distribution. To rebuild a controller manually, quit it, move the old `.app` aside, and rerun the installer. Removing the app does not delete artifacts; turn Launch at Login off before removal.

## Parallel decks

Use one browser tab and one agent chat per deck, all on the same instance and MCP endpoint. Feedback, slides, notes, approvals, visibility and revisions are scoped by deck. Each agent must use the intended `deck_id`; a live reviewer chat binds to one deck at a time. Distinct browser drafts are stored separately, so one tab does not replace another deck's recovery data.

Workspace rules, learned preferences, designs, library connections and the global design inbox are shared. Concurrent changes to the same saved slide or rule document use revision checks and may require reconciliation. Separate instances are useful for different workspaces, not required for separate decks.
