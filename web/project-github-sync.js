/* Manage an operator-configured GitHub binding from its project. */
((root) => {
  'use strict';
  const doc = root.document;
  const session = root.ArtifactSession;
  const workspace = '/api/workspaces/' + encodeURIComponent(session.workspace);
  const dialog = doc.createElement('dialog');
  dialog.className = 'studio-share project-github-sync';
  dialog.setAttribute('aria-labelledby', 'project-github-sync-title');
  dialog.innerHTML = `
    <header class="studio-share-header">
      <h2 id="project-github-sync-title">GitHub sync</h2>
      <button type="button" aria-label="Close GitHub sync">${root.SlideIcons.svg('close')}</button>
    </header>
    <p data-project></p>
    <p data-destination></p>
    <details data-sources><summary>Synced sources</summary><ul></ul></details>
    <div class="project-sync-controls">
      <label><input type="checkbox" data-enabled> Automatically sync both ways</label>
      <button type="button" class="studio-share-system" data-check>Sync now</button>
    </div>
    <p class="studio-share-status" role="status"></p>
    <p class="studio-share-error" role="alert" hidden></p>
    <section aria-label="Sync results"></section>`;
  doc.body.append(dialog);

  const enabled = dialog.querySelector('[data-enabled]');
  const check = dialog.querySelector('[data-check]');
  const status = dialog.querySelector('[role=status]');
  const error = dialog.querySelector('[role=alert]');
  const sources = dialog.querySelector('[data-sources] ul');
  const results = dialog.querySelector('section');
  let projects = new Set();
  let openRequest = 0;
  let current = null;

  async function exchange(path, method = 'GET', body) {
    session.assertActive();
    const response = session.observe(await root.fetch(path, {
      method, cache: 'no-store',
      ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
    }));
    session.assertActive();
    let data;
    try { data = await response.json(); }
    catch { throw new Error('The sync result is unavailable. Reopen GitHub sync to check progress.'); }
    if (!response.ok) {
      const reason = new Error(typeof data.detail === 'string' ? data.detail : 'GitHub sync failed.');
      reason.status = response.status;
      throw reason;
    }
    return data;
  }

  function paragraph(value) {
    const element = doc.createElement('p');
    element.textContent = value;
    return element;
  }

  function action(label) {
    const element = doc.createElement('button');
    element.type = 'button';
    element.textContent = label;
    return element;
  }

  function commitLink(repository, commit, label) {
    if (!/^[\w-]+\/[\w.-]+$/.test(repository) || !/^[a-f0-9]{40,64}$/.test(commit)) return null;
    const link = doc.createElement('a');
    link.href = 'https://github.com/' + repository + '/commit/' + commit;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = label + ' ' + commit.slice(0, 8);
    return link;
  }

  function conflictActions(context, report) {
    const controls = doc.createElement('div');
    controls.className = 'project-sync-choices';
    const confirmation = doc.createElement('div');
    confirmation.className = 'project-sync-confirmation';
    confirmation.hidden = true;
    for (const [choice, label] of [['studio', 'Keep Studio'], ['github', 'Keep GitHub']]) {
      const button = action(label);
      button.disabled = !report.conflict || (choice === 'github' && !report.can_use_github);
      button.addEventListener('click', () => {
        const consequence = choice === 'studio'
          ? 'Replace all files in the paired GitHub folder with this Studio revision? GitHub retains the earlier commit.'
          : 'Replace all files in this Studio artifact with the GitHub source? Studio retains the earlier revision.';
        const cancel = action('Cancel');
        const confirm = action(label === 'Keep Studio' ? 'Replace GitHub source' : 'Replace Studio source');
        cancel.addEventListener('click', () => {
          confirmation.hidden = true;
          button.focus();
        });
        confirm.addEventListener('click', () => {
          void run(context, '/' + encodeURIComponent(report.artifact) + '/resolve', 'POST',
            {conflict: report.conflict, choice});
        });
        confirmation.replaceChildren(paragraph(consequence), cancel, confirm);
        confirmation.hidden = false;
        cancel.focus();
      });
      controls.append(button);
    }
    return [controls, confirmation];
  }

  function reportCard(context, value, report) {
    const row = doc.createElement('article');
    const title = doc.createElement('h3');
    title.textContent = report.title || context.names.get(report.artifact) || report.artifact;
    row.append(title);
    const summaries = {
      upload: 'Uploaded to GitHub.',
      download: 'Updated in Studio from GitHub.',
      merge: 'Combined changes from Studio and GitHub.',
      unchanged: 'Studio and GitHub match.',
      conflict: 'Needs a decision',
      blocked: 'Sync paused for this source',
    };
    row.append(paragraph(summaries[report.action]));
    if (report.detail) row.append(paragraph(report.detail));
    if (report.action !== 'conflict') return row;

    const revisions = doc.createElement('ul');
    const studio = doc.createElement('li');
    studio.textContent = report.studio_revision ? 'Studio revision ' + report.studio_revision : 'Studio source';
    revisions.append(studio);
    for (const [commit, label] of [
      [report.commit, 'Current GitHub commit'],
      [report.baseline_commit, 'Last shared commit'],
    ]) {
      const link = commitLink(value.repository, commit, label);
      if (!link) continue;
      const entry = doc.createElement('li');
      entry.append(link);
      revisions.append(entry);
    }
    row.append(revisions);
    if (report.changed_count) {
      const changes = doc.createElement('details');
      const summary = doc.createElement('summary');
      summary.textContent = report.changed_count + (report.changed_count === 1 ? ' differing file' : ' differing files');
      const files = doc.createElement('ul');
      for (const path of report.changed_files) {
        const item = doc.createElement('li');
        item.textContent = path;
        files.append(item);
      }
      if (report.changed_count > report.changed_files.length) {
        const more = doc.createElement('li');
        more.textContent = 'And ' + (report.changed_count - report.changed_files.length) + ' more';
        files.append(more);
      }
      changes.append(summary, files);
      row.append(changes);
    }
    row.append(...conflictActions(context, report));
    return row;
  }

  function render(context, value) {
    context.value = value;
    enabled.checked = value.enabled;
    const destination = dialog.querySelector('[data-destination]');
    destination.textContent = value.repository + ' · ' + value.branch;
    sources.replaceChildren(...Object.entries(value.artifacts).map(([id, path]) => {
      const item = doc.createElement('li');
      item.textContent = (context.names.get(id) || id) + ' · ' + path;
      return item;
    }));
    status.textContent = value.last_checked
      ? 'Last checked ' + new Date(value.last_checked * 1000).toLocaleString()
      : 'This project has not synced yet.';
    error.textContent = value.error || '';
    error.hidden = !value.error;
    results.replaceChildren(...value.reports.map(report => reportCard(context, value, report)));
  }

  function active(context) {
    return context !== null && current === context && dialog.open;
  }

  function failure(reason) {
    error.textContent = reason.message;
    error.hidden = false;
  }

  async function run(context, suffix, method, body) {
    if (!active(context) || context.busy) return;
    context.busy = true;
    const controls = [...dialog.querySelectorAll('input, button:not(header button)')];
    const available = controls.filter(control => !control.disabled);
    available.forEach(control => { control.disabled = true; });
    error.hidden = true;
    if (suffix) status.textContent = 'Syncing sources…';
    const focused = doc.activeElement;
    const token = ++context.generation;
    try {
      const value = await exchange(context.path + suffix, method, body);
      if (!active(context) || token !== context.generation) return;
      render(context, value);
    } catch (reason) {
      if (!active(context)) return;
      render(context, context.value);
      if (reason.status === 409) {
        try {
          const latest = await exchange(context.path);
          if (active(context)) render(context, latest);
        } catch {}
      }
      if (active(context)) failure(reason);
    } finally {
      if (active(context)) {
        available.forEach(control => { control.disabled = false; });
        context.busy = false;
        if (!focused?.isConnected || doc.activeElement === doc.body) check.focus();
      }
    }
  }

  enabled.addEventListener('change', () => void run(current, '', 'PUT', {enabled: enabled.checked}));
  check.addEventListener('click', () => void run(current, '/check', 'POST'));
  dialog.querySelector('header button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    root.clearInterval(current?.timer);
    current?.anchor?.focus();
    current = null;
    enabled.disabled = false;
    check.disabled = false;
  });

  void session.get().then(async info => {
    if (info.collaboration_url !== '/together' || (info.scoped_editor && !info.workspace_owner)) return;
    projects = new Set(await exchange(workspace + '/github-sync-projects'));
  }).catch(() => {});

  root.ProjectGitHubSync = {
    available(project) { return projects.has(project); },
    async open(project, title, anchor, names) {
      if (dialog.open || !projects.has(project)) return;
      const attempt = ++openRequest;
      const path = workspace + '/projects/' + encodeURIComponent(project) + '/github-sync';
      const value = await exchange(path);
      if (attempt !== openRequest || dialog.open || !anchor?.isConnected) return;
      const context = {path, anchor, names, busy: false, generation: 0, polling: false};
      current = context;
      dialog.querySelector('[data-project]').textContent = title;
      dialog.querySelector('[data-sources]').open = false;
      render(context, value);
      dialog.showModal();
      context.timer = root.setInterval(async () => {
        if (!active(context) || context.busy || context.polling || doc.hidden
          || results.contains(doc.activeElement) || results.querySelector('details[open]')) return;
        const generation = context.generation;
        context.polling = true;
        try {
          const latest = await exchange(path);
          if (active(context) && !context.busy && generation === context.generation
            && !results.contains(doc.activeElement) && !results.querySelector('details[open]')
            && JSON.stringify(latest) !== JSON.stringify(context.value)) render(context, latest);
        } catch {}
        finally { context.polling = false; }
      }, 15000);
    },
  };
})(window);
