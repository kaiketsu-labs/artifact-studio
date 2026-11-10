/* Keep the current artifact selected when moving from the editor to collaboration. */
((root) => {
  'use strict';
  const button = document.querySelector('[data-collaborate]');
  if (!button) return;

  const sessionInfo = root.ArtifactSession.get();
  async function scopedRequest(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    root.ArtifactSession.observe(response);
    root.ArtifactSession.assertActive();
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The request failed.');
    return result;
  }

  /** Connect an agent to the selected scope using the standard editor menu. */
  function connectAgent({kind, id, title, anchor, mcpUrl}) {
    if (document.querySelector('.studio-agent[open]')) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-share studio-agent';
    dialog.setAttribute('aria-labelledby', 'studio-agent-title');
    dialog.setAttribute('data-voice-exclude', '');
    dialog.innerHTML = `<header class="studio-share-header"><h2 id="studio-agent-title">Agent connection</h2><button type="button" aria-label="Close agent connection" data-agent-close>${root.SlideIcons.svg('close')}</button></header>
      <p class="studio-invite-note" data-agent-scope></p>
      <form class="studio-invite-form"><label>Agent name<input name="name" required maxlength="80" autofocus></label>
      <button type="submit" class="studio-share-system">Connect agent</button></form>
      <div class="studio-invite-result" hidden>
        <label for="studio-scope-token">Private token</label><div><input id="studio-scope-token" type="password" readonly><button type="button" class="studio-share-system" data-copy-token>Copy token</button></div>
        <p class="studio-invite-note">Set ARTIFACT_STUDIO_AGENT_TOKEN in your agent environment. The token is shown once.</p>
        <label for="studio-scope-codex">Codex</label><div><input id="studio-scope-codex" readonly><button type="button" class="studio-share-system" data-copy-codex>Copy</button></div>
        <label for="studio-scope-claude">Claude Code</label><div><input id="studio-scope-claude" readonly><button type="button" class="studio-share-system" data-copy-claude>Copy</button></div>
      </div>
      <div class="studio-access-list" aria-label="Your connections"></div>
      <p class="studio-share-status" role="status"></p><p class="studio-share-error" role="alert" hidden></p>`;
    dialog.querySelector('[data-agent-scope]').textContent = title;
    const form = dialog.querySelector('form'), output = dialog.querySelector('.studio-invite-result');
    const status = dialog.querySelector('[role="status"]'), error = dialog.querySelector('[role="alert"]');
    const failed = reason => { error.textContent = reason.message; error.hidden = false; };
    const agentsPath = '/api/workspaces/default/agents';
    async function loadAgents() {
      const agents = await scopedRequest(agentsPath);
      const list = dialog.querySelector('.studio-access-list');
      list.replaceChildren(...agents.filter(agent => agent.kind === kind && agent.resource === id).map(agent => {
        const row = document.createElement('div'), label = document.createElement('span'), button = document.createElement('button');
        label.textContent = agent.name; button.textContent = 'Revoke'; button.type = 'button';
        button.addEventListener('click', async () => {
          button.disabled = true;
          try { await scopedRequest('/api/agents/' + encodeURIComponent(agent.id) + '/revoke', {}); await loadAgents(); }
          catch (reason) { failed(reason); button.disabled = false; }
        });
        row.append(label, button);
        return row;
      }));
    }
    for (const name of ['token', 'codex', 'claude']) {
      dialog.querySelector('[data-copy-' + name + ']').addEventListener('click', async () => {
        const field = dialog.querySelector('#studio-scope-' + name);
        try { await navigator.clipboard.writeText(field.value); status.textContent = 'Copied.'; }
        catch { if (name === 'token') field.type = 'text'; field.focus(); field.select(); status.textContent = 'Copy the selected text.'; }
      });
    }
    form.addEventListener('submit', async event => {
      event.preventDefault(); const submit = event.submitter; submit.disabled = true; error.hidden = true;
      try {
        const result = await scopedRequest(agentsPath, {kind, resource: id, name: new FormData(form).get('name')});
        const url = result.mcp_url || mcpUrl;
        const serverName = 'artifact-studio-shared-' + result.id.slice(0,8);
        dialog.querySelector('#studio-scope-token').value = result.token;
        dialog.querySelector('#studio-scope-codex').value = 'codex mcp add ' + serverName + ' --url ' + url + ' --bearer-token-env-var ARTIFACT_STUDIO_AGENT_TOKEN';
        dialog.querySelector('#studio-scope-claude').value = "claude mcp add --transport http " + serverName + " " + url + " --header 'Authorization: Bearer ${ARTIFACT_STUDIO_AGENT_TOKEN}'";
        form.hidden = true; output.hidden = false; output.querySelector('button').focus();
        await loadAgents();
      } catch (reason) { failed(reason); }
      finally { submit.disabled = false; }
    });
    dialog.querySelector('[data-agent-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { dialog.remove(); anchor?.focus(); }, {once: true});
    document.body.append(dialog); dialog.showModal(); loadAgents().catch(failed);
  }

  /** Create a one-person Studio invitation without leaving the current editor. */
  async function openInvitation({kind, id, title, anchor}) {
    if (!root.StudioInvitation.available || document.querySelector('.studio-invite[open]')) return;
    const trigger = anchor?.closest("button,a") || anchor?.querySelector("button,a") || document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-share studio-invite';
    dialog.setAttribute('data-voice-exclude', '');
    dialog.setAttribute('aria-labelledby', 'studio-invite-title');
    dialog.innerHTML = `<header class="studio-share-header"><h2 id="studio-invite-title">Share link</h2><button type="button" data-invite-close aria-label="Close share link">${root.SlideIcons.svg('close')}</button></header>
      <div class="studio-share-item"><span class="studio-share-file">${root.SlideIcons.svg(kind === 'project' ? 'folder' : 'share')}</span><div><strong data-invite-title></strong><span>${kind === 'workspace' ? 'All projects and artifacts in this workspace' : kind === 'project' ? 'All current and future artifacts in this project' : 'This artifact only'}</span></div></div>
      <form class="studio-invite-form">
        <label>Email address<input type="email" name="email" required autofocus autocomplete="email" maxlength="254"></label>
        <label>Access<select name="role"><option value="view">Can view</option><option value="comment">Can comment</option><option value="edit">Can edit</option></select></label>
        <button type="submit" class="studio-share-system">Create link</button>
      </form>
      <p class="studio-invite-note">Only this email can accept. The link expires in 7 days. The recipient must be allowed to sign in.</p>
      <div class="studio-invite-result" hidden><label for="studio-invitation-link">Invitation link</label><div><input id="studio-invitation-link" readonly spellcheck="false"><button type="button" class="studio-share-system">Copy link</button></div></div>
      <details class="studio-invite-manage"><summary>Manage access</summary><div class="studio-access-list" aria-live="polite"></div></details>
      <p class="studio-share-status" role="status"></p><p class="studio-share-error" role="alert" hidden></p>`;
    dialog.querySelector('[data-invite-title]').textContent = title;
    const form = dialog.querySelector('form');
    const output = dialog.querySelector('.studio-invite-result');
    const link = output.querySelector('input');
    const notice = dialog.querySelector('[role="status"]');
    const error = dialog.querySelector('[role="alert"]');
    const manage = dialog.querySelector('.studio-invite-manage');
    async function loadAccess() {
      const query = new URLSearchParams({kind, resource: id});
      const access = await scopedRequest('/api/workspaces/default/access?' + query);
      const list = manage.querySelector('.studio-access-list');
      const rows = [...access.grants, ...access.invitations.map(item => ({...item, pending: true}))];
      list.replaceChildren(...rows.map(item => {
        const row = document.createElement('div'), label = document.createElement('span'), button = document.createElement('button');
        label.textContent = item.email + ' · ' + ({view:'Can view',comment:'Can comment',edit:'Can edit'}[item.role]);
        button.textContent = item.inherited ? 'Manage parent' : item.pending ? 'Cancel link' : 'Remove';
        button.type = 'button';
        button.addEventListener('click', async () => {
          if (item.inherited) {
            dialog.close();
            await openInvitation({kind:item.kind,id:item.resource,title:item.kind==='project'?'Parent project':'Workspace',anchor:trigger});
            return;
          }
          button.disabled = true;
          try { await scopedRequest('/api/workspaces/default/access/' + encodeURIComponent(item.id) + '/revoke', {kind,resource:id}); await loadAccess(); }
          catch (reason) { error.textContent=reason.message;error.hidden=false;button.disabled=false; }
        });
        row.append(label, button);
        return row;
      }));
      if (!rows.length) list.textContent = 'Only you have access.';
    }
    manage.addEventListener('toggle', () => {
      if (manage.open) loadAccess().catch(reason => { error.textContent=reason.message;error.hidden=false; });
    });
    dialog.querySelector('[data-invite-close]').addEventListener('click', () => dialog.close());
    output.querySelector('button').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(link.value); notice.textContent = 'Link copied.'; }
      catch { link.focus(); link.select(); notice.textContent = 'Copy the selected link.'; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const submit = event.submitter;
      if (submit.disabled) return;
      submit.disabled = true; error.hidden = true; output.hidden = true; notice.textContent = '';
      const fields = new FormData(form);
      try {
        const result = await scopedRequest('/api/workspaces/default/invitations', {
          kind, resource: id, email: fields.get('email'), role: fields.get('role'),
        });
        const invitation = new URL(result.url);
        invitation.pathname = '/';
        link.value = invitation.href;
        output.hidden = false;
        if (manage.open) await loadAccess();
        root.requestAnimationFrame(() => { if (dialog.open) output.querySelector('button').focus(); });
      } catch (reason) { error.textContent = reason.message; error.hidden = false; }
      finally { submit.disabled = false; }
    });
    dialog.addEventListener('close', () => { dialog.remove(); trigger?.focus(); }, {once: true});
    document.body.append(dialog);
    dialog.showModal();
  }
  /** Accept a new link from the normal library, before opening its scoped resource. */
  function showJoin(session, token) {
    const dialog = document.createElement('dialog');
    dialog.className = 'studio-share';
    dialog.setAttribute('aria-labelledby', 'studio-join-title');
    dialog.innerHTML = `<header class="studio-share-header"><h2 id="studio-join-title">Join Artifact Studio</h2><button type="button" aria-label="Close invitation">${root.SlideIcons.svg('close')}</button></header>
      <p class="studio-invite-note"></p><form class="studio-invite-form"><button class="studio-share-system" type="submit">Join</button></form>
      <p class="studio-share-error" role="alert" hidden></p>`;
    dialog.querySelector('.studio-invite-note').textContent = session.email ? 'Join as ' + session.email : 'Join with your signed-in account.';
    dialog.querySelector('[aria-label="Close invitation"]').addEventListener('click', () => dialog.close());
    dialog.querySelector('form').addEventListener('submit', async event => {
      event.preventDefault(); const submit = event.submitter;
      const error = dialog.querySelector('[role="alert"]');
      submit.disabled = true; error.hidden = true;
      try {
        const scope = await scopedRequest('/api/invitations/accept', {token});
        const query = new URLSearchParams();
        if (scope.workspace === 'default') {
          if (scope.kind === 'project' || scope.kind === 'artifact') query.set(scope.kind, scope.resource);
          root.location.replace('/' + (query.size ? '?' + query : ''));
        } else {
          query.set('workspace', scope.workspace);
          if (scope.kind === 'artifact') query.set('artifact', scope.resource);
          root.location.replace('/together?' + query);
        }
      } catch (reason) {
        error.textContent = reason.message; error.hidden = false; submit.disabled = false;
      }
    });
    dialog.addEventListener('close', () => {
      const url = new URL(root.location.href); url.hash = '';
      root.history.replaceState(null, '', url); dialog.remove();
    }, {once:true});
    document.body.append(dialog); dialog.showModal();
  }

  root.StudioInvitation = {available: false, open: openInvitation};

  sessionInfo.then(session => {

    if (session.collaboration_url !== '/together') return;
    root.SlideIcons.button(button, 'chat', 'Collaborate', true);
    button.hidden = !!session.scoped_editor;
    const token = new URLSearchParams(root.location.hash.slice(1)).get('invite');
    if (token && token.length <= 200) showJoin(session, token);
    root.StudioInvitation.available = !session.scoped_editor;
    const open = (action = '') => {
      const source = new URLSearchParams(root.location.search);
      const target = new URL(session.collaboration_url, root.location.origin);
      target.searchParams.set('workspace', 'default');
      const deck = source.get('deck');
      const artifact = button.closest('.hub-top') ? button.dataset.artifact : deck;
      if (artifact) target.searchParams.set('artifact', artifact);
      if (deck && root.location.hash) target.searchParams.set('slide', root.location.hash.slice(1));
      if (action) target.searchParams.set('action', action);
      root.location.assign(target);
    };
    const chatURL = new URL(root.location.href);
    const shared = chatURL.searchParams.get('chat') === 'shared';
    if (shared && !session.scoped_editor) root.SlideIcons.button(button, 'chat', 'Local review', true);
    button.addEventListener('click', () => {
      const current = new URL(root.location.href);
      const artifact = current.searchParams.get('deck') || button.dataset.artifact;
      if (!artifact || session.scoped_editor) { open(); return; }
      if (!root.dispatchEvent(new CustomEvent('studio:before-conversation-change', {cancelable: true}))) return;
      if (shared) current.searchParams.delete('chat');
      else current.searchParams.set('chat', 'shared');
      root.location.assign(current);
    });

    document.querySelectorAll('#connect-button, [data-hub-connect]').forEach(entry => {
      entry.addEventListener('click', event => {
        event.preventDefault();
        event.stopImmediatePropagation();
        const params = new URLSearchParams(root.location.search);
        const artifact = params.get('deck') || button.dataset.artifact;
        const project = params.get('project');
        if (!artifact && (!project || project === 'shared-with-me') && session.scoped_editor) {
          const dialog = document.createElement('dialog');
          dialog.className = 'studio-share';
          dialog.innerHTML = '<header class="studio-share-header"><h2>Agent connection</h2><button type="button" aria-label="Close">×</button></header><p class="studio-invite-note">Open a shared artifact to connect an agent.</p>';
          dialog.querySelector('button').onclick = () => dialog.close();
          dialog.addEventListener('close', () => dialog.remove(), {once:true});
          document.body.append(dialog); dialog.showModal();
          return;
        }
        const kind = artifact ? 'artifact' : project ? 'project' : 'workspace';
        const title = (document.querySelector('.hub-edit-heading h1, #sorter-presentation-title, .hub-heading h1')?.textContent || '').trim()
          || (kind === 'workspace' ? 'Current workspace' : kind === 'project' ? 'Current project' : 'Current artifact');
        connectAgent({kind,id:artifact||project||'',title,anchor:entry,mcpUrl:root.location.origin+'/mcp'});

      }, {capture: true});
    });
  }).catch(() => {});

  root.addEventListener('artifact-session-expired', () => { button.hidden = true; root.StudioInvitation.available = false; });
})(window);
