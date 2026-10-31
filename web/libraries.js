/* Reference-only repositories remain separate from explicitly opted-in automatic snapshots. */
window.SlideLibraries = {
  init({api, toast, escape, currentDeck}) {
    const $ = id => document.getElementById(id);
    const {svg, button} = window.SlideIcons;
    button($('libraries-button'), 'folder', 'Reference libraries', true);
    button($('close-libraries'), 'close', 'Close');
    button($('add-library'), 'plus', 'Connect library', true);
    let busy = false, timer = null, rendered = '', revision = 0;
    function syncOption(){const github=/^(?:https:\/\/github\.com\/)?[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/.test($('library-location').value.trim());$('library-sync-setting').hidden=!github;if(!github)$('library-sync').checked=false;}
    async function render(force = false) {
      const request = ++revision;
      const [libraries,statuses] = await Promise.all([api('/api/libraries'),api('/api/library-sync')]);
      if (request !== revision || busy && !force) return;
      const states=new Map(statuses.map(item=>[item.library_id,item]));
      const markup = libraries.map(item => `<article class="library-row">
        <span class="library-symbol">${svg(item.kind === 'github' ? 'github' : 'folder')}</span>
        <div class="library-description"><strong>${escape(item.id === 'collection' ? 'This Mac · Presentations' : item.location)}</strong>
        <small>${item.kind === 'github' ? (item.auto_sync_enabled ? 'Private · automatic sync' : item.sync_enabled ? 'Private · manual sync' : 'GitHub · reference only') : 'Local reference'}</small>
        ${item.kind==='github'?`<label class="library-auto"><input type="checkbox" data-auto-sync="${escape(item.id)}" ${item.auto_sync_enabled?'checked':''}><span>Auto-sync all presentations</span></label>${item.auto_sync_enabled?`<small class="sync-state sync-${escape(states.get(item.id)?.phase||'pending')}" title="${escape(states.get(item.id)?.error||states.get(item.id)?.last_synced_at||'')}">${({pending:'Waiting for saved changes',syncing:'Syncing…',synced:'Synced',error:'Sync needs attention'})[states.get(item.id)?.phase]||'Waiting for saved changes'}</small>${states.get(item.id)?.error?`<small class="sync-error" role="alert">${escape(states.get(item.id).error)}</small>`:''}`:''}`:''}</div>
        <div class="library-actions">${item.kind === 'github' ? `<a class="icon-control icon-only" data-library="${escape(item.id)}" data-action="open" href="https://github.com/${escape(item.location)}" target="_blank" rel="noopener" aria-label="Open repository" title="Open repository">${svg('external')}</a><button type="button" data-library="${escape(item.id)}" data-action="refresh" aria-label="Refresh references" title="Refresh references">${svg('refresh')}</button>` : ''}
        ${item.auto_sync_enabled?`<button type="button" data-library="${escape(item.id)}" data-action="retry" aria-label="Sync workspace now" title="Sync workspace now">${svg('upload')}</button>`:item.sync_enabled ? `<button type="button" data-library="${escape(item.id)}" data-action="current" aria-label="Sync current presentation" title="Sync current presentation">${svg('upload')}</button><button type="button" data-library="${escape(item.id)}" data-action="all" aria-label="Sync all presentations" title="Sync all presentations">${svg('batch')}</button>` : ''}</div></article>`).join('');
      if (markup === rendered) return;
      const list = $('library-list'), focused = document.activeElement;
      const focus = list.contains(focused) ? focused?.dataset : null;
      list.innerHTML = markup;
      rendered = markup;
      working(busy);
      const selector = focus?.autoSync ? `[data-auto-sync="${CSS.escape(focus.autoSync)}"]` : focus?.library ? `[data-library="${CSS.escape(focus.library)}"][data-action="${CSS.escape(focus.action)}"]` : null;
      if (selector && !busy) list.querySelector(selector)?.focus();
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
      syncOption();
      try { await render(); working(busy); } catch (error) { $('library-error').textContent = error.message; }
      clearInterval(timer);timer=setInterval(async()=>{if(busy||$('library-dialog').contains(document.activeElement)&&document.activeElement.matches('input'))return;try{await render();}catch(error){$('library-error').textContent=error.message;}},3000);
    });
    $('close-libraries').addEventListener('click', () => $('library-dialog').close());
    $('library-dialog').addEventListener('close',()=>clearInterval(timer));
    $('library-location').addEventListener('input',syncOption);
    $('library-list').addEventListener('change',async event=>{
      const control=event.target.closest('input[data-auto-sync]');if(!control||busy)return;
      const enabled=control.checked;working(true,enabled?'Enabling automatic sync…':'Switching to references only…');$('library-error').textContent='';
      try{await api(`/api/libraries/${encodeURIComponent(control.dataset.autoSync)}/settings`,{auto_sync_enabled:enabled});await render(true);}
      catch(error){control.checked=!enabled;$('library-error').textContent=error.message;}
      finally{working(false);}
    });
    $('library-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const location = $('library-location').value.trim();
      if (!location) return;
      working(true, 'Connecting…');
      $('library-error').textContent = '';
      try {
        await api('/api/libraries', {location, auto_sync_enabled: $('library-sync').checked});
        $('library-form').reset();
        syncOption();
        await render(true);
        toast('Library connected.');
      } catch (error) { $('library-error').textContent = error.message; }
      finally { working(false); }
    });
    $('library-list').addEventListener('click', async event => {
      const control = event.target.closest('button[data-library]');
      if (!control || busy) return;
      const {library, action} = control.dataset;
      if (action === 'current' && !currentDeck()) return;
      working(true, action === 'refresh' ? 'Refreshing references…' : 'Syncing to private repository…');
      $('library-error').textContent = '';
      try {
        if(action==='retry'){await api(`/api/libraries/${encodeURIComponent(library)}/retry`,{});await render(true);toast('Sync queued.');return;}
        const result = await api(`/api/libraries/${encodeURIComponent(library)}/${action === 'refresh' ? 'refresh' : 'sync'}`,
          action === 'refresh' ? {} : {deck_id: action === 'current' ? currentDeck().id : null});
        if (action === 'refresh') toast('References updated.');
        else toast(result.files_changed ? `Synced ${result.files_changed} file${result.files_changed === 1 ? '' : 's'}.` : 'Already synced.');
      } catch (error) { $('library-error').textContent = error.message; }
      finally { working(false); }
    });
  }
};
