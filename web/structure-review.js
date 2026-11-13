/* Review structural proposals without changing the presentation. */
((root) => {
  'use strict';
  function create({api, escape, button, dialog}) {
    let deckId = '', snapshot = null, activeId = '', loading = false, sequence = 0, requestId = '', stale = false;
    let saved = {};
    try { saved = JSON.parse(root.sessionStorage.getItem('structure-review-drafts') || '{}'); } catch {}
    const drafts = new Map(Object.entries(saved));
    const persist = () => { try { root.sessionStorage.setItem('structure-review-drafts', JSON.stringify(Object.fromEntries(drafts))); } catch {} };
    const path = id => `/api/workspaces/${encodeURIComponent(root.ArtifactSession.workspace || 'default')}/artifacts/${encodeURIComponent(id)}/structure-proposals`;
    const active = () => snapshot?.proposals.find(item => item.id === activeId);
    const draftKey = () => `${deckId}/${activeId}`;
    function saveDraft() {
      if (!activeId || !dialog.open) return;
      drafts.set(draftKey(), {decision: dialog.querySelector('input[name="structure-decision"]:checked')?.value || '', message: dialog.querySelector('textarea')?.value || ''}); persist();
    }
    function renderButton() {
      const pending = snapshot?.proposals.filter(item => item.status === 'pending').length || 0;
      button.hidden = !pending;
      button.textContent = `Review slide structure (${pending})`;
    }
    function list(ids, names) {
      return `<ol>${ids.map(id => `<li>${escape(names.get(id) || id)}</li>`).join('')}</ol>`;
    }
    function render() {
      const proposal = active();
      if (!proposal) { dialog.close(); return; }
      const names = new Map([...snapshot.slides, ...proposal.missing, ...proposal.extra].map(item => [item.id, item.title]));
      const value = drafts.get(draftKey()) || {decision: '', message: ''};
      const baseChanged = JSON.stringify(proposal.base_slide_ids) !== JSON.stringify(snapshot.slide_ids) || proposal.base_sections_hash !== snapshot.sections_hash;
      const changes = (items, label) => items.length ? `<p><strong>${label}:</strong> ${items.map(item => escape(item.title)).join(', ')}</p>` : '';
      dialog.innerHTML = `<header><h2 id="structure-review-title">Review slide structure</h2><button type="button" data-close aria-label="Close" title="Close">×</button></header>
        <div class="structure-review-body"><label>Proposal <select data-proposal aria-label="Proposal">${snapshot.proposals.map(item => `<option value="${escape(item.id)}" ${item.id === activeId ? 'selected' : ''}>${escape(item.summary)}${item.status === 'decided' ? ' · Decided' : ''}</option>`).join('')}</select></label>
        <p>${escape(proposal.summary)}</p><div class="structure-comparison"><section><h3>Current order</h3>${list(snapshot.slide_ids, names)}</section><section><h3>Proposed order</h3>${list(proposal.proposed_slide_ids, names)}</section></div>
        ${changes(proposal.missing, 'Missing from proposal')}${changes(proposal.extra, 'Additional slides')}
        ${baseChanged ? '<p class="structure-warning">The presentation has changed since this proposal. Ask the agent for an updated proposal before accepting.</p>' : ''}
        ${stale ? '<p class="structure-warning" role="alert" tabindex="-1">The presentation or proposal changed. Review the latest comparison before sending.</p>' : ''}
        ${proposal.status === 'decided' ? `<p>Decision: ${escape(({keep:'Keep current order', request_change:'Request changes', accept:'Accept proposal'})[proposal.decision] || proposal.decision)}${proposal.reply ? ` — ${escape(proposal.reply)}` : ''}</p>${value.message ? `<label>Unsent message<textarea readonly rows="3">${escape(value.message)}</textarea></label>` : ''}` : `<form><fieldset><legend>Your decision</legend>${[['keep','Keep current order'],['request_change','Request changes'],['accept','Accept proposal']].map(([id,label]) => `<label><input type="radio" name="structure-decision" value="${id}" ${value.decision === id && !(id === 'accept' && baseChanged) ? 'checked' : ''} ${id === 'accept' && baseChanged ? 'disabled' : ''}> ${label}</label>`).join('')}</fieldset><label>Message to the proposing agent <textarea maxlength="10000" rows="3">${escape(value.message)}</textarea></label><p class="structure-error" role="alert"></p><button type="submit" class="primary">Send decision</button></form>`}</div>`;
      dialog.querySelector('[data-close]').addEventListener('click', () => { saveDraft(); dialog.close(); });
      dialog.querySelector('[data-proposal]').addEventListener('change', event => { saveDraft(); activeId = event.target.value; stale = false; requestId = ''; render(); });
      const form = dialog.querySelector('form');
      if (form) {
        form.addEventListener('input', () => { saveDraft(); requestId = ''; });
        form.addEventListener('submit', async event => {
          event.preventDefault(); saveDraft(); const value = drafts.get(draftKey());
          if (!value.decision) { form.querySelector('.structure-error').textContent = 'Choose a decision.'; return; }
          const submit = form.querySelector('button[type="submit"]'); submit.disabled = true;
          requestId ||= root.crypto.randomUUID();
          try {
            await api(`${path(deckId)}/${encodeURIComponent(activeId)}/decision`, {decision: value.decision, message: value.message, expected_slide_ids: snapshot.slide_ids, expected_sections_hash: snapshot.sections_hash, request_id: requestId});
            drafts.delete(draftKey()); persist(); requestId = ''; stale = false; await reload(); render();
          } catch (error) {
            if (error.status === 409) { stale = true; requestId = ''; try { await reload(); render(); dialog.querySelector('.structure-warning[role=alert]')?.focus(); } catch { form.querySelector('.structure-error').textContent = 'Could not load the latest comparison. Your decision is kept here.'; submit.disabled = false; } }
            else { form.querySelector('.structure-error').textContent = error.message; submit.disabled = false; }
          }
        });
      }
    }
    async function reload() {
      const current = deckId, serial = ++sequence;
      const result = await api(path(current));
      if (current !== deckId || serial !== sequence) return;
      snapshot = result; renderButton();
    }
    button.addEventListener('click', async () => {
      if (loading) return; loading = true; button.disabled = true;
      try { await reload(); activeId = snapshot.proposals.find(item => item.status === 'pending')?.id || snapshot.proposals[0]?.id || ''; if (activeId) { stale = false; render(); dialog.showModal(); } }
      catch (error) { button.title = error.message; }
      finally { loading = false; button.disabled = false; }
    });
    dialog.addEventListener('cancel', saveDraft);
    dialog.addEventListener('close', () => { if (!button.hidden) button.focus(); });
    return {
      async sync(id, editable) {
        if (!editable || !id) { deckId = ''; snapshot = null; ++sequence; button.hidden = true; if (dialog.open) { saveDraft(); dialog.close(); } return; }
        if (deckId !== id) { if (dialog.open) { saveDraft(); dialog.close(); } deckId = id; snapshot = null; activeId = ''; button.hidden = true; }
        if (!dialog.open) try { await reload(); } catch { /* A missing or unavailable endpoint must not interrupt editing. */ }
      },
    };
  }
  root.StructureReview = {create};
})(window);
