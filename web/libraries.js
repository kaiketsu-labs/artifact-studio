/* Reference libraries and deliberate private snapshots share the workspace helpers. */
window.SlideLibraries = {
  init({api, toast, escape, currentDeck}) {
    const $ = id => document.getElementById(id);
    const {svg, button} = window.SlideIcons;
    button($('libraries-button'), 'folder', 'Reference libraries');
    button($('close-libraries'), 'close', 'Close');
    button($('add-library'), 'plus', 'Connect library', true);
    let busy = false;
    async function render() {
      const libraries = await api('/api/libraries');
      $('library-list').innerHTML = libraries.map(item => `<article class="library-row">
        <span class="library-symbol">${svg(item.kind === 'github' ? 'github' : 'folder')}</span>
        <div class="library-description"><strong>${escape(item.id === 'collection' ? 'This Mac · Presentations' : item.location)}</strong>
        <small>${item.kind === 'github' ? (item.sync_enabled ? 'Private · reference & sync' : 'GitHub · reference only') : 'Local reference'}</small></div>
        <div class="library-actions">${item.kind === 'github' ? `<a class="icon-control icon-only" href="https://github.com/${escape(item.location)}" target="_blank" rel="noopener" aria-label="Open repository" title="Open repository">${svg('external')}</a><button type="button" data-library="${escape(item.id)}" data-action="refresh" aria-label="Refresh references" title="Refresh references">${svg('refresh')}</button>` : ''}
        ${item.sync_enabled ? `<button type="button" data-library="${escape(item.id)}" data-action="current" aria-label="Sync current presentation" title="Sync current presentation">${svg('upload')}</button><button type="button" data-library="${escape(item.id)}" data-action="all" aria-label="Sync all presentations" title="Sync all presentations">${svg('batch')}</button>` : ''}</div></article>`).join('');
    }
    function working(value, message = '') {
      busy = value;
      $('library-status').textContent = message;
      $('library-dialog').setAttribute('aria-busy', String(value));
      $('library-dialog').querySelectorAll('button:not(#close-libraries),input').forEach(element => element.disabled = value);
    }
    $('libraries-button').addEventListener('click', async () => {
      $('library-dialog').showModal();
      $('library-error').textContent = '';
      try { await render(); working(busy); } catch (error) { $('library-error').textContent = error.message; }
    });
    $('close-libraries').addEventListener('click', () => $('library-dialog').close());
    $('library-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const location = $('library-location').value.trim();
      if (!location) return;
      working(true, 'Connecting…');
      $('library-error').textContent = '';
      try {
        await api('/api/libraries', {location, sync_enabled: $('library-sync').checked});
        $('library-form').reset();
        await render();
        toast('Library connected. Your agent can use its references.');
      } catch (error) { $('library-error').textContent = error.message; }
      finally { working(false); }
    });
    $('library-list').addEventListener('click', async event => {
      const control = event.target.closest('button[data-library]');
      if (!control || busy) return;
      const {library, action} = control.dataset;
      if (action === 'current' && !currentDeck()) return;
      working(true, action === 'refresh' ? 'Refreshing references…' : 'Syncing to your private repository…');
      $('library-error').textContent = '';
      try {
        const result = await api(`/api/libraries/${encodeURIComponent(library)}/${action === 'refresh' ? 'refresh' : 'sync'}`,
          action === 'refresh' ? {} : {deck_id: action === 'current' ? currentDeck().id : null});
        if (action === 'refresh') toast('References updated.');
        else toast(result.files_changed ? `Synced ${result.files_changed} files.` : 'Everything is already synced.');
      } catch (error) { $('library-error').textContent = error.message; }
      finally { working(false); }
    });
  }
};
