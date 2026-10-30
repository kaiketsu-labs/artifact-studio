/* Recoverable selector removal uses local metadata, with persistent restore access. */
window.SlidePresentations = {
  init({api, toast, escape, currentDeck, retainDraft, reload, forget, recover}) {
    const $ = id => document.getElementById(id);
    const {svg, button} = window.SlideIcons;
    button($('remove-presentation'), 'archive', 'Remove presentation from list');
    button($('removed-presentations'), 'restore', 'Removed presentations');
    button($('close-removed'), 'close', 'Close');
    let busy = false, blocked = false, generation = 0, removed = [];

    function update(sending = blocked) {
      blocked = sending;
      $('remove-presentation').disabled = busy || blocked || !currentDeck();
      $('deck-picker').disabled = busy || blocked || !$('deck-picker').options.length;
      $('removed-list').querySelectorAll('button').forEach(control => control.disabled = busy || blocked);
    }
    function renderRemoved() {
      $('removed-presentations').hidden = !removed.length;
      $('removed-presentations').title = `Removed presentations (${removed.length})`;
      $('removed-list').innerHTML = removed.map(item => `<div class="removed-row"><span>${escape(item.title)}</span><button type="button" class="icon-control icon-only" data-restore="${escape(item.id)}" aria-label="Restore ${escape(item.title)}" title="Restore ${escape(item.title)}">${svg('restore')}</button></div>`).join('') || '<p>No removed presentations.</p>';
      update();
    }
    async function refresh() {
      const token = generation, items = await api('/api/removed-presentations');
      if (token === generation) { removed = items; renderRemoved(); }
    }
    async function restore(id) {
      if (busy || blocked) return;
      const item = removed.find(entry => entry.id === id);
      busy = true; update(); retainDraft();
      try {
        await api(`/api/decks/${encodeURIComponent(id)}/restore`, {});
        generation++; removed = removed.filter(entry => entry.id !== id);
        renderRemoved(); if (item) recover(item);
        if (!removed.length) $('removed-dialog').close();
        try { await reload(id); toast('Presentation restored.'); }
        catch (error) { toast(`Restored. ${error.message}`, true); }
      } finally { busy = false; update(); }
    }
    $('remove-presentation').addEventListener('click', async () => {
      const item = currentDeck();
      if (!item || busy || blocked) return;
      busy = true; update(); retainDraft();
      try {
        await api(`/api/decks/${encodeURIComponent(item.id)}/archive`, {});
        generation++; removed = [...removed.filter(entry => entry.id !== item.id), {id:item.id, title:item.title}];
        forget(item.id); renderRemoved();
        let issue = '';
        try { await reload(); } catch (error) { issue = error.message; }
        toast(issue ? `Removed. ${issue}` : 'Removed from list.', !!issue, {label:'Undo', run:() => restore(item.id)});
        requestAnimationFrame(() => (currentDeck() ? $('deck-switcher') : $('new-button')).focus());
      } catch (error) { toast(error.message, true); }
      finally { busy = false; update(); }
    });
    $('removed-presentations').addEventListener('click', async () => {
      $('removed-error').textContent = '';
      $('removed-dialog').showModal();
      try { await refresh(); } catch (error) { $('removed-error').textContent = error.message; }
    });
    $('removed-list').addEventListener('click', async event => {
      const control = event.target.closest('[data-restore]');
      if (!control) return;
      $('removed-error').textContent = '';
      try { await restore(control.dataset.restore); }
      catch (error) { $('removed-error').textContent = error.message; }
    });
    $('close-removed').addEventListener('click', () => $('removed-dialog').close());
    return {refresh, update, isBusy:() => busy};
  }
};
