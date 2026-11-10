const $ = id => document.getElementById(id);
const state = {session: null, workspace: '', artifact: '', slide: '', channel: 'shared', catalog: null,
  current: null, generation: 0, messages: [], accessScope: null, accessWorkspace: '', outgoing: new Map(), recipients: new Map(), hasEarlier: false};
const roleNames = {view: 'Can view', comment: 'Can comment', edit: 'Can edit'};
const node = (tag, text, className) => {const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n;};
let noticeTimer;
function notice(text) { $('notice').textContent = text; $('notice').hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 7000); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {const error = new Error(data.detail || `Request failed (${response.status})`); error.status = response.status; throw error;}
  return data;
}
const base = (workspace = state.workspace) => `/api/workspaces/${encodeURIComponent(workspace)}`;
const artifactBase = () => `${base()}/artifacts/${encodeURIComponent(state.artifact)}`;
const draftKey = () => ['studio-chat-v1', state.session?.person, state.workspace, state.artifact, state.slide, state.channel].join(':');
const recipientKey = () => [state.workspace, state.artifact, state.channel].join(':');
function saveDraft() {if (state.artifact) {try {localStorage.setItem(draftKey(), $('message').value);} catch { /* Private browsing may disable storage. */ }}}
function loadDraft() {try {$('message').value = localStorage.getItem(draftKey()) || '';} catch {$('message').value = '';}}
function clearEditor() {state.current = null; state.messages = []; state.hasEarlier = false; $('editor').hidden = true; $('empty').hidden = false; $('preview').removeAttribute('src'); $('messages').replaceChildren(); $('messages').dataset.signature = ''; $('slides').dataset.signature = '';}
function updateURL() {const url = new URL(location.href); url.search = new URLSearchParams({workspace: state.workspace, ...(state.artifact ? {artifact: state.artifact} : {})}); history.replaceState(null, '', url);}
function sharingBadge(text) {return node('span', text, `badge ${text.includes('Shared') || text.includes('shared') ? 'shared' : ''}`);}

async function boot() {
  state.session = await api('/api/session');
  $('account').textContent = state.session.email;
  $('new-workspace').hidden = !state.session.can_create;
  $('workspace').replaceChildren(...state.session.workspaces.map(item => new Option(item.title, item.id)));
  const invitation = new URLSearchParams(location.hash.slice(1)).get('invite');
  $('invitation').hidden = !invitation;
  $('invite-account').textContent = `Join as ${state.session.email}.`;
  const desired = new URLSearchParams(location.search).get('workspace');
  const selected = state.session.workspaces.find(item => item.id === (desired || state.workspace)) || state.session.workspaces[0];
  if (selected) await selectWorkspace(selected.id, new URLSearchParams(location.search).get('artifact'));
  else {clearEditor(); $('empty').querySelector('p').textContent = invitation ? 'Join the invitation above to start collaborating.' : 'No shared workspaces yet. Ask the owner for an invitation.'; $('empty-connect').hidden = true; $('new-project').hidden = true; $('workspace-access').hidden = true;}
}
async function selectWorkspace(workspace, artifact = '') {
  saveDraft(); state.generation++; state.workspace = workspace; state.artifact = ''; state.slide = ''; clearEditor();
  $('workspace').value = workspace;
  const generation = state.generation;
  const catalog = await api(`${base()}/catalog`);
  if (generation !== state.generation) return;
  state.catalog = catalog;
  $('workspace-access').hidden = !catalog.owner;
  $('workspace-sharing').replaceWith(Object.assign(sharingBadge(catalog.sharing), {id: 'workspace-sharing'}));
  $('new-project').hidden = !catalog.can_create_project;
  $('empty-connect').hidden = false;
  renderCatalog(); updateURL();
  const selected = catalog.artifacts.find(item => item.id === artifact) || catalog.artifacts[0];
  if (selected) await selectArtifact(selected.id);
}
function renderCatalog() {
  const groups = [...state.catalog.projects, {id: '', title: 'Shared with me'}];
  $('catalog').replaceChildren();
  for (const project of groups) {
    const artifacts = state.catalog.artifacts.filter(item => item.project_id === project.id);
    if (!project.id && !artifacts.length) continue;
    const section = node('section'); const heading = node('div', null, 'project-heading');
    heading.append(node('strong', project.title));
    if (project.id && state.catalog.owner) {
      const button = node('button', 'Share', 'quiet'); button.setAttribute('aria-label', `Share project ${project.title}`);
      button.addEventListener('click', () => openAccess({kind: 'project', resource: project.id, title: project.title}).catch(noticeError)); heading.append(button);
    }
    section.append(heading);
    if (project.sharing && project.sharing !== 'Private') section.append(sharingBadge(project.sharing));
    for (const artifact of artifacts) {
      const button = node('button', null, 'artifact-link'); button.dataset.artifact = artifact.id; button.setAttribute('aria-current', String(artifact.id === state.artifact));
      button.append(node('span', artifact.title), sharingBadge(artifact.sharing));
      button.addEventListener('click', () => selectArtifact(artifact.id).catch(noticeError)); section.append(button);
    }
    if (!artifacts.length) section.append(node('p', 'Your agent can create artifacts here.', 'small subtle'));
    $('catalog').append(section);
  }
}
async function selectArtifact(artifact) {
  saveDraft(); state.generation++; state.artifact = artifact; state.slide = ''; clearEditor(); updateURL(); renderCatalog();
  await refresh(true);
}
function renderSlides() {
  const slides = state.current.slides;
  if (!slides.some(slide => slide.id === state.slide)) state.slide = slides[0]?.id || '';
  $('slides').hidden = !slides.length;
  const signature = JSON.stringify([slides.map(slide => [slide.id, slide.title]), state.slide]);
  if ($('slides').dataset.signature !== signature) {
  const focused = document.activeElement?.dataset.slide;
  $('slides').dataset.signature = signature;
  $('slides').replaceChildren(...slides.map((slide, index) => {
    const button = node('button', `${index + 1}  ${slide.title}`); button.setAttribute('aria-current', String(slide.id === state.slide));
    button.dataset.slide = slide.id;
    button.addEventListener('click', () => {saveDraft(); state.slide = slide.id; loadDraft(); renderSlides(); renderPreview();}); return button;
  }));
  if (focused) [...$('slides').children].find(button => button.dataset.slide === focused)?.focus();
  }
  $('target-label').textContent = slides.find(slide => slide.id === state.slide)?.title || state.current.title;
}
function renderPreview() {
  const url = new URL(`${artifactBase()}/preview`, location.origin);
  if (state.slide) url.searchParams.set('slide_id', state.slide);
  url.searchParams.set('version', state.current.content_hash);
  $('preview').src = url.href; $('preview').parentElement.classList.toggle('document', !state.current.slides.length);
}
function renderAgents() {
  const selected = state.recipients.get(recipientKey()) || '';
  const agents = state.current.agents.filter(item => state.channel === 'shared' || item.mine);
  const options = state.channel === 'shared' ? [new Option('Discuss', '')] : [new Option('Choose your agent', '')];
  options.push(...agents.map(item => new Option(`${item.mine ? item.name : `${item.email} · ${item.name}`} (${item.status})`, item.id)));
  if (JSON.stringify([...$('recipient').options].map(item => [item.value, item.text])) !== JSON.stringify(options.map(item => [item.value, item.text]))) $('recipient').replaceChildren(...options);
  if (agents.some(item => item.id === selected)) $('recipient').value = selected;
  else $('recipient').value = '';
  $('recipient').disabled = state.current.access.role !== 'edit';
  const listening = state.current.agents.filter(item => item.status === 'listening').length;
  $('people').textContent = listening ? `${listening} agent${listening === 1 ? '' : 's'} listening` : 'Connect an agent to work together';
}
async function refresh(first = false) {
  if (!state.artifact) return;
  const generation = state.generation, endpoint = artifactBase(), channel = state.channel;
  const [current, messages] = await Promise.all([api(`${endpoint}/state`), api(`${endpoint}/chat?channel=${channel}`)]);
  if (generation !== state.generation) return;
  const changed = current.content_hash !== state.current?.content_hash;
  state.current = current;
  if (!state.messages.length || first) state.hasEarlier = messages.length === 100;
  const latestIds = new Set(messages.map(message => message.id));
  state.messages = [...state.messages.filter(message => !latestIds.has(message.id) && message.cursor < (messages[0]?.cursor || 0)), ...messages];
  const completed = new Set(state.messages.filter(message => message.reply_to).map(message => message.reply_to));
  state.messages.forEach(message => {if (completed.has(message.id)) message.completed = true;});
  $('empty').hidden = true; $('editor').hidden = false; $('artifact-title').textContent = current.title;
  const item = state.catalog.artifacts.find(item => item.id === current.id);
  $('artifact-path').textContent = state.catalog.projects.find(project => project.id === item?.project_id)?.title || 'Shared artifact';
  $('artifact-access').textContent = current.access.can_manage ? 'Share' : 'Access';
  $('access-role').textContent = roleNames[current.access.role];
  $('revision').textContent = `Revision ${current.revision}`;
  const canComment = current.access.role !== 'view';
  $('message').disabled = !canComment; $('send').disabled = !canComment; $('batch').disabled = !canComment;
  if (!canComment) $('message').placeholder = 'You have view access';
  else $('message').placeholder = state.channel === 'private' ? 'Ask your agent…' : 'Discuss this artifact…';
  renderAgents(); renderSlides(); renderMessages();
  if (first) loadDraft();
  if (first || changed) renderPreview();
}
function renderMessages() {
  const list = $('messages'); const atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
  const signature = JSON.stringify(state.messages);
  if (list.dataset.signature !== signature) {
    list.dataset.signature = signature;
    list.replaceChildren(...state.messages.map(message => {
      const mine = message.author === state.session.person;
      const isAgent = message.author !== message.person;
      const row = node('article', null, `message${mine ? ' mine' : ''}${message.released ? '' : ' draft'}`);
      const header = node('header');
      header.append(node('strong', isAgent ? state.current.agents.find(agent => agent.id === message.author)?.name || 'Agent' : mine ? 'You' : message.author_name), node('time', new Date(message.created * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})));
      if (!message.released) header.append(node('span', 'Draft'));
      row.append(header);
      if (message.target?.label) row.append(node('div', message.target.label, 'anchor'));
      row.append(node('div', message.message, 'body'));
      if (message.assigned && !message.completed) row.append(node('span', message.released ? 'Requested' : 'Waiting for Send batch', 'subtle small'));
      return row;
    }));
    if (!state.messages.length) list.append(node('p', state.channel === 'shared' ? 'Talk with your collaborators here. Choose an agent when you want a change.' : 'A private conversation with your agents. Changes they make are visible to everyone.', 'empty-chat'));
    if (atEnd) list.scrollTop = list.scrollHeight;
  }
  $('draft-actions').hidden = !state.current.draft_counts?.[state.channel];
  $('release').disabled = state.current.access.role === 'view';
  $('earlier').hidden = !state.hasEarlier;
}
function switchChannel(channel) {
  saveDraft(); state.generation++; state.channel = channel; state.messages = []; $('messages').dataset.signature = ''; $('messages').replaceChildren();
  document.querySelectorAll('[data-channel]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.channel === channel)));
  document.querySelectorAll('[data-channel]').forEach(button => {button.tabIndex = button.dataset.channel === channel ? 0 : -1;});
  $('privacy-hint').textContent = channel === 'private' ? 'Only you and your scoped agents. Artifact edits are shared.' : 'Visible to everyone with access.';
  loadDraft(); refresh().catch(noticeError);
}
async function openAccess(scope) {
  state.accessScope = scope; state.accessWorkspace = state.workspace;
  $('access-title').textContent = scope.title || 'Workspace access';
  $('scope-description').textContent = scope.kind === 'workspace' ? 'Access applies to every project and artifact in this workspace.' : scope.kind === 'project' ? 'Access applies to current and future artifacts in this project.' : 'Access applies to this artifact only. Parent grants still apply.';
  $('access-error').textContent = ''; $('invite-result').hidden = true;
  if (!$('access-dialog').open) $('access-dialog').showModal();
  await loadAccess();
}
async function loadAccess() {
  const scope = state.accessScope;
  const access = await api(`${base(state.accessWorkspace)}/access?${new URLSearchParams({kind: scope.kind, resource: scope.resource})}`);
  $('invite-form').hidden = !access.can_manage;
  const rows = [];
  if (access.can_manage) {
    rows.push(node('p', 'You own this workspace.', 'small subtle'));
    for (const grant of [...access.grants, ...access.invitations.map(item => ({...item, pending: true}))]) {
      const row = node('div', null, 'access-row'); const info = node('div');
      info.append(node('strong', grant.email), node('p', `${roleNames[grant.role]}${grant.pending ? ' · Invitation pending' : grant.inherited ? ` · Inherited from ${grant.kind}` : ' · Direct access'}`)); row.append(info);
      if (grant.inherited) {
        const button = node('button', 'Manage parent'); button.addEventListener('click', () => openAccess({kind: grant.kind, resource: grant.resource, title: `${grant.kind === 'workspace' ? 'Workspace' : 'Project'} access`}).catch(noticeError)); row.append(button);
      } else {
        const button = node('button', grant.pending ? 'Cancel invite' : 'Remove'); button.addEventListener('click', async () => {
          button.disabled = true;
          try {await api(`${base(state.accessWorkspace)}/access/${grant.id}/revoke`, {kind: scope.kind, resource: scope.resource}); await loadAccess(); await refresh();}
          catch (error) {$('access-error').textContent = error.message; button.disabled = false;}
        }); row.append(button);
      }
      rows.push(row);
    }
    if (!access.grants.length && !access.invitations.length) rows.push(node('p', 'Only you have access.', 'small subtle'));
  } else for (const source of access.sources) rows.push(node('p', `${roleNames[source.role]} · ${source.kind === scope.kind && source.resource === scope.resource ? 'Direct access' : `Inherited from ${source.kind}`}`, 'small'));
  $('access-list').replaceChildren(...rows);
}
function connectionScopes() {
  const options = [];
  if (state.artifact) options.push({kind: 'artifact', resource: state.artifact, title: state.current.title});
  for (const item of state.catalog?.projects || []) options.push({kind: 'project', resource: item.id, title: `Project · ${item.title}`});
  if (state.catalog?.can_create_project || state.catalog?.owner) options.push({kind: 'workspace', resource: '', title: 'Entire workspace'});
  return options;
}
function openAgents() {
  if (!state.workspace) return;
  $('agent-error').textContent = ''; $('agent-result').hidden = true; $('agent-token').value = ''; $('agent-form').hidden = false;
  $('agent-scope').replaceChildren(...connectionScopes().map(scope => new Option(scope.title, JSON.stringify(scope))));
  $('my-agents').replaceChildren();
  $('agent-dialog').showModal();
  api(`${base()}/agents`).then(agents => {
  if (!$('agent-dialog').open) return;
  for (const agent of agents) {
    const row = node('div', null, 'agent-row'); const info = node('div'); info.append(node('strong', agent.name), node('p', `${agent.kind} connection`));
    const button = node('button', 'Disconnect'); button.addEventListener('click', async () => {
      try {await api(`/api/agents/${agent.id}/revoke`, {}); await refresh(); row.remove(); notice('Agent disconnected. Other agents remain connected.');} catch (error) {$('agent-error').textContent = error.message;}
    }); row.append(info, button); $('my-agents').append(row);
  }
  }).catch(error => {if ($('agent-dialog').open) $('agent-error').textContent = error.message;});
}
async function copyField(id, button) {try {await navigator.clipboard.writeText($(id).value); const label = button.textContent; button.textContent = 'Copied'; setTimeout(() => {button.textContent = label;}, 1500);} catch {$(id).focus(); $(id).select(); notice('Select and copy this field.');}}
function noticeError(error) {notice(error.message || String(error));}

$('workspace').addEventListener('change', () => selectWorkspace($('workspace').value).catch(noticeError));
$('workspace-access').addEventListener('click', () => openAccess({kind: 'workspace', resource: '', title: 'Workspace access'}).catch(noticeError));
$('artifact-access').addEventListener('click', () => openAccess({kind: 'artifact', resource: state.artifact, title: state.current.title}).catch(noticeError));
for (const id of ['connect-agent', 'empty-connect']) $(id).addEventListener('click', openAgents);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
$('agent-dialog').addEventListener('close', () => {$('agent-token').value = ''; $('agent-result').hidden = true;});
document.querySelectorAll('[data-channel]').forEach(button => button.addEventListener('click', () => switchChannel(button.dataset.channel)));
document.querySelectorAll('[data-channel]').forEach(button => button.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault(); const channel = event.key === 'Home' ? 'shared' : event.key === 'End' ? 'private' : state.channel === 'shared' ? 'private' : 'shared';
  switchChannel(channel); $(`${channel}-tab`).focus();
}));
$('recipient').addEventListener('change', () => state.recipients.set(recipientKey(), $('recipient').value));
$('message').addEventListener('input', saveDraft);
$('message').addEventListener('keydown', event => {if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {event.preventDefault(); $('composer').requestSubmit();}});
$('composer').addEventListener('submit', async event => {
  event.preventDefault(); if (!$('message').value.trim()) return;
  if (state.channel === 'private' && !$('recipient').value) {notice('Connect or choose your agent first.'); return;}
  const key = draftKey(), endpoint = artifactBase(), generation = state.generation;
  const payload = {channel: state.channel, message: $('message').value.trim(), assigned: $('recipient').value, released: !$('batch').checked, content_hash: state.current.content_hash,
    target: state.slide ? {kind: 'slide', slide_id: state.slide, label: state.current.slides.find(slide => slide.id === state.slide)?.title || state.slide} : null};
  const prior = state.outgoing.get(key); const signature = JSON.stringify(payload);
  const requestId = prior?.signature === signature ? prior.requestId : crypto.randomUUID();
  state.outgoing.set(key, {signature, requestId}); $('send').disabled = true;
  try {await api(`${endpoint}/chat`, {...payload, request_id: requestId}); state.outgoing.delete(key);
    if (generation === state.generation) {$('message').value = ''; saveDraft(); await refresh(); $('messages').scrollTop = $('messages').scrollHeight;}}
  catch (error) {noticeError(error); if (error.status === 409) {state.outgoing.delete(key); await refresh();}}
  finally {if (generation === state.generation) $('send').disabled = state.current?.access.role === 'view';}
});
$('release').addEventListener('click', async () => {try {await api(`${artifactBase()}/release?channel=${state.channel}`, {}); state.messages.forEach(message => {if (message.person === state.session.person) message.released = true;}); await refresh();} catch (error) {noticeError(error);}});
$('earlier').addEventListener('click', async () => {const generation = state.generation; try {const messages = await api(`${artifactBase()}/chat?${new URLSearchParams({channel: state.channel, before: state.messages[0].cursor})}`); if (generation === state.generation) {const ids = new Set(state.messages.map(item => item.id)); state.messages = [...messages.filter(item => !ids.has(item.id)), ...state.messages]; state.hasEarlier = messages.length === 100; renderMessages();}} catch (error) {noticeError(error);}});
$('invite-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('access-error').textContent = '';
  try {const result = await api(`${base(state.accessWorkspace)}/invitations`, {kind: state.accessScope.kind, resource: state.accessScope.resource, email: $('invite-email').value, role: $('invite-role').value}); $('invite-link').value = result.url; $('invite-result').hidden = false; await loadAccess();}
  catch (error) {$('access-error').textContent = error.message;} finally {button.disabled = false;}
});
$('agent-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('agent-error').textContent = '';
  try {const scope = JSON.parse($('agent-scope').value); const result = await api(`${base()}/agents`, {kind: scope.kind, resource: scope.resource, name: $('agent-name').value});
    $('agent-token').value = result.token; $('agent-command').value = `codex mcp add artifact-studio --url ${result.mcp_url} --bearer-token-env-var ARTIFACT_STUDIO_AGENT_TOKEN`;
    $('claude-command').value = `claude mcp add --transport http artifact-studio ${result.mcp_url} --header 'Authorization: Bearer \${ARTIFACT_STUDIO_AGENT_TOKEN}'`;
    $('agent-form').hidden = true; $('agent-result').hidden = false; await refresh();}
  catch (error) {$('agent-error').textContent = error.message;} finally {button.disabled = false;}
});
$('copy-token').addEventListener('click', event => copyField('agent-token', event.currentTarget));
$('copy-invite').addEventListener('click', event => copyField('invite-link', event.currentTarget));
$('join').addEventListener('click', async () => {
  $('join').disabled = true;
  try {const token = new URLSearchParams(location.hash.slice(1)).get('invite'); const scope = await api('/api/invitations/accept', {token}); history.replaceState(null, '', `/?${new URLSearchParams({workspace: scope.workspace, ...(scope.kind === 'artifact' ? {artifact: scope.resource} : {})})}`); await boot(); notice('You joined the shared workspace.');}
  catch (error) {notice(error.status === 404 ? 'This invitation is unavailable for this account. Check the invited email or ask for a new link.' : error.message);} finally {$('join').disabled = false;}
});
for (const kind of ['workspace', 'project']) $(`new-${kind}`).addEventListener('click', () => {$('name-dialog').dataset.kind = kind; $('name-title').textContent = `New ${kind}`; $('new-name').value = ''; $('name-error').textContent = ''; $('name-dialog').showModal(); $('new-name').focus();});
$('name-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {const workspace = $('name-dialog').dataset.kind === 'workspace'; const result = await api(workspace ? '/api/workspaces' : `${base()}/projects`, {title: $('new-name').value}); $('name-dialog').close();
    if (workspace) {history.replaceState(null, '', `/?workspace=${encodeURIComponent(result.id)}`); await boot();} else await selectWorkspace(state.workspace, state.artifact);}
  catch (error) {$('name-error').textContent = error.message;} finally {button.disabled = false;}
});
$('appearance').addEventListener('click', () => {const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches; document.documentElement.dataset.theme = dark ? 'light' : 'dark';});
window.addEventListener('pagehide', saveDraft);
let polling = false, catalogRefresh = 0;
async function refreshCatalog() {
  const workspace = state.workspace;
  const catalog = await api(`${base(workspace)}/catalog`);
  if (workspace !== state.workspace) return;
  if (JSON.stringify(catalog) !== JSON.stringify(state.catalog)) {
    state.catalog = catalog; renderCatalog();
    $('workspace-sharing').textContent = catalog.sharing;
    $('workspace-sharing').classList.toggle('shared', catalog.sharing !== 'Private');
    if (!state.artifact && catalog.artifacts.length) await selectArtifact(catalog.artifacts[0].id);
  }
}
for (const id of ['access-dialog', 'agent-dialog']) $(id).addEventListener('close', () => {catalogRefresh = 0;});
setInterval(async () => {if (polling || document.hidden || !state.workspace) return; polling = true;
  try {await refresh(); if (Date.now() - catalogRefresh > 10000) {await refreshCatalog(); catalogRefresh = Date.now();}}
  catch (error) {if (error.status === 404 || error.status === 401) {saveDraft(); state.generation++; state.artifact = ''; clearEditor(); document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close()); notice('Access changed. Select a workspace you can access.'); await boot().catch(noticeError);} else notice('Connection interrupted. Your draft is saved; retrying…');} finally {polling = false;}
}, 2500);
boot().catch(noticeError);
