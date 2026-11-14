/* Review structural proposals without changing the presentation. */
((root) => {
  'use strict';
  function create({api, readResponse, escape, button, dialog, identity = () => ''}) {
    let deckId = '', snapshot = null, activeId = '', loading = false, sequence = 0, viewEpoch = 0, requestId = '', stale = false;
    let currentIdentity = '', pendingSnapshot = null, inFlight = null, cached = null;
    let saved = {};
    try { saved = JSON.parse(root.sessionStorage.getItem('structure-review-drafts-v2') || '{}'); } catch {}
    const drafts = new Map(Object.entries(saved));
    const persist = () => { try { root.sessionStorage.setItem('structure-review-drafts-v2', JSON.stringify(Object.fromEntries(drafts))); } catch {} };
    const path = id => `/api/workspaces/${encodeURIComponent(root.ArtifactSession.workspace || 'default')}/artifacts/${encodeURIComponent(id)}/structure-proposals`;
    const active = () => snapshot?.proposals.find(item => item.id === activeId);
    const draftKey = () => JSON.stringify([root.ArtifactSession.workspace || 'default', currentIdentity, deckId, activeId]);
    function saveDraft() {
      if (!activeId || !dialog.open || !dialog.querySelector('form')) return;
      drafts.set(draftKey(), {decision: dialog.querySelector('input[name="structure-decision"]:checked')?.value || '', message: dialog.querySelector('textarea')?.value || ''}); persist();
    }
    function renderButton() {
      const pending = snapshot?.proposals.filter(item => item.status === 'pending').length || 0;
      button.hidden = !snapshot?.proposals.length;
      button.textContent = pending ? `Review slide structure (${pending})` : 'Slide structure decisions';
    }
    function list(ids, names) {
      return `<ol>${ids.map(id => `<li>${escape(names.get(id) || id)}</li>`).join('')}</ol>`;
    }
    function showLiveStatus() {
      const target = dialog.querySelector('[data-live-status]');
      if (!target) return;
      if (Boolean(target.firstElementChild) === Boolean(pendingSnapshot)) return;
      target.innerHTML = pendingSnapshot ? '<div class="structure-warning" role="status">The presentation changed while you were reviewing. <button type="button" data-reload>Review latest</button></div>' : '';
      target.querySelector('[data-reload]')?.addEventListener('click', async () => {
        saveDraft(); const current = deckId, proposal = activeId, epoch = viewEpoch;
        try {
          if (!await reload(true) || !dialog.open || deckId !== current || activeId !== proposal || viewEpoch !== epoch) return;
          pendingSnapshot = null; stale = true; ++viewEpoch; render();
          dialog.querySelector('.structure-warning[role=alert]')?.focus();
        } catch (error) { target.textContent = error.message; }
      });
    }
    function render() {
      const proposal = active();
      if (!proposal) { dialog.close(); return; }
      const names = new Map([...snapshot.slides, ...proposal.missing, ...proposal.extra].map(item => [item.id, item.title]));
      const value = drafts.get(draftKey()) || {decision: '', message: ''};
      const reviewedIds = [...snapshot.slide_ids], reviewedSectionsHash = snapshot.sections_hash;
      const renderedDeck = deckId, renderedProposal = activeId, renderedKey = draftKey(), epoch = viewEpoch;
      const stillCurrent = () => dialog.open && deckId === renderedDeck && activeId === renderedProposal && viewEpoch === epoch;
      const baseChanged = JSON.stringify(proposal.base_slide_ids) !== JSON.stringify(snapshot.slide_ids) || proposal.base_sections_hash !== snapshot.sections_hash;
      const changes = (items, label) => items.length ? `<p><strong>${label}:</strong> ${items.map(item => escape(item.title)).join(', ')}</p>` : '';
      dialog.innerHTML = `<header><h2 id="structure-review-title">Review slide structure</h2><button type="button" data-close aria-label="Close" title="Close">×</button></header>
        <div class="structure-review-body"><label>Proposal <select data-proposal aria-label="Proposal">${snapshot.proposals.map(item => `<option value="${escape(item.id)}" ${item.id === activeId ? 'selected' : ''}>${escape(item.summary)}${item.status === 'decided' ? ' · Decided' : ''}</option>`).join('')}</select></label>
        <p>${escape(proposal.summary)}</p><p>Proposals and decisions are shared with collaborators.</p><div class="structure-comparison"><section><h3>Current order</h3>${list(snapshot.slide_ids, names)}</section><section><h3>Proposed order</h3>${list(proposal.proposed_slide_ids, names)}</section></div>
        ${changes(proposal.missing, 'Only in Studio')}${changes(proposal.extra, 'Only in proposal')}
        ${baseChanged ? '<p class="structure-warning">The presentation has changed since this proposal. Ask the agent for an updated proposal before accepting.</p>' : ''}
        <div data-live-status></div>
        ${stale ? '<p class="structure-warning" role="alert" tabindex="-1">The presentation or proposal changed. Review the latest comparison before sending.</p>' : ''}
        ${proposal.status === 'decided' ? `<p role="status" data-recorded-decision tabindex="-1">Decision: ${escape(({keep:'Keep current order', request_change:'Request changes', accept:'Accept proposal'})[proposal.decision] || proposal.decision)}${proposal.reply ? ` — ${escape(proposal.reply)}` : ''}</p>${value.decision ? `<p>Unsent decision: ${escape(({keep:'Keep current order', request_change:'Request changes', accept:'Accept proposal'})[value.decision])}</p>` : ''}${value.message ? `<label>Unsent message<textarea readonly rows="3">${escape(value.message)}</textarea></label>` : ''}` : `<form><p>Studio sends your decision to the proposing agent.</p><fieldset><legend>Your decision</legend>${[['keep','Keep current order'],['request_change','Request changes'],['accept','Accept proposal']].map(([id,label]) => `<label><input type="radio" name="structure-decision" value="${id}" ${value.decision === id && !(id === 'accept' && baseChanged) ? 'checked' : ''} ${id === 'accept' && baseChanged ? 'disabled' : ''}> ${label}</label>`).join('')}</fieldset><label>Message to the proposing agent <textarea maxlength="10000" rows="3">${escape(value.message)}</textarea></label><p class="structure-error" role="alert"></p><button type="submit" class="primary">Send decision</button></form>`}</div>`;
      showLiveStatus();
      dialog.querySelector('[data-close]').addEventListener('click', () => { saveDraft(); dialog.close(); });
      dialog.querySelector('[data-proposal]').addEventListener('change', event => { saveDraft(); activeId = event.target.value; ++viewEpoch; pendingSnapshot = null; stale = false; requestId = ''; render(); });
      const form = dialog.querySelector('form');
      if (form) {
        form.addEventListener('input', () => { saveDraft(); requestId = ''; });
        form.addEventListener('submit', async event => {
          event.preventDefault(); saveDraft(); const value = drafts.get(renderedKey);
          if (!value.decision) { form.querySelector('.structure-error').textContent = 'Choose a decision.'; return; }
          const submit = form.querySelector('button[type="submit"]'); submit.disabled = true;
          requestId ||= root.crypto.randomUUID();
          try {
            await api(`${path(renderedDeck)}/${encodeURIComponent(renderedProposal)}/decision`, {decision: value.decision, message: value.message, expected_slide_ids: reviewedIds, expected_sections_hash: reviewedSectionsHash, request_id: requestId});
            if (!stillCurrent()) return;
            drafts.delete(renderedKey); persist();
            requestId = ''; stale = false; await reload(true);
            if (stillCurrent()) { render(); dialog.querySelector('[data-recorded-decision]')?.focus({preventScroll:true}); }
          } catch (error) {
            if (!stillCurrent()) return;
            if (error.status === 409) { stale = true; requestId = ''; try { await reload(true); if (!stillCurrent()) return; render(); dialog.querySelector('.structure-warning[role=alert]')?.focus(); } catch { if (!stillCurrent()) return; form.querySelector('.structure-error').textContent = 'Could not load the latest comparison. Your decision is kept here.'; submit.disabled = false; } }
            else { form.querySelector('.structure-error').textContent = error.message; submit.disabled = false; }
          }
        });
      }
    }
    async function read(current, fresh = false) {
      if (inFlight) {
        if (!fresh && inFlight.id === current && inFlight.owner === currentIdentity) return inFlight.promise;
        try { await inFlight.promise; } catch {}
        if (current !== deckId) return null;
        return read(current);
      }
      const owner = currentIdentity, saved = cached;
      const request = async () => {
        if (!readResponse) return api(path(current));
        const validator = saved?.id === current && saved.owner === owner ? saved.validator : '';
        const {result, validator: received} = await readResponse(path(current), validator);
        const fresh = result || (validator ? saved.result : null);
        if (fresh && current === deckId && owner === currentIdentity) {
          cached = {id: current, owner, result: fresh, validator: received};
        }
        return fresh;
      };
      const pending = {id: current, owner, promise: request()}; inFlight = pending;
      try { return await pending.promise; } finally { if (inFlight === pending) inFlight = null; }
    }
    async function reload(fresh = false) {
      const current = deckId, serial = ++sequence;
      const result = await read(current, fresh);
      if (!result || current !== deckId || serial !== sequence) return false;
      snapshot = result; renderButton(); return true;
    }
    button.addEventListener('click', async () => {
      if (loading) return; loading = true; button.disabled = true;
      const current = deckId, epoch = viewEpoch;
      try { if (!await reload() || current !== deckId || epoch !== viewEpoch) return; activeId = snapshot.proposals.find(item => item.status === 'pending')?.id || snapshot.proposals[0]?.id || ''; if (activeId) { stale = false; render(); dialog.showModal(); } }
      catch (error) { button.title = error.message; }
      finally { loading = false; button.disabled = false; }
    });
    dialog.addEventListener('cancel', saveDraft);
    dialog.addEventListener('close', () => { ++viewEpoch; if (!button.hidden) button.focus(); });
    return {
      async sync(id, editable) {
        if (!editable || !id) { if (dialog.open) { saveDraft(); dialog.close(); } ++viewEpoch; deckId = ''; currentIdentity = ''; requestId = ''; pendingSnapshot = null; snapshot = null; cached = null; ++sequence; button.hidden = true; return; }
        if (deckId !== id || currentIdentity !== identity()) { if (dialog.open) { saveDraft(); dialog.close(); } ++viewEpoch; deckId = id; currentIdentity = identity(); requestId = ''; pendingSnapshot = null; snapshot = null; cached = null; activeId = ''; button.hidden = true; }
        const current = deckId, epoch = viewEpoch;
        try {
          if (!dialog.open) { await reload(); return; }
          const result = await read(current);
          if (!result || current !== deckId || epoch !== viewEpoch || !dialog.open) return;
          const latest = result.proposals.find(item => item.id === activeId);
          if (latest?.status === 'decided' && active()?.status !== 'decided') {
            saveDraft(); snapshot = result; pendingSnapshot = null; ++viewEpoch; renderButton(); render();
            dialog.querySelector('[data-recorded-decision]')?.focus({preventScroll:true});
          } else if (JSON.stringify(result.slide_ids) !== JSON.stringify(snapshot.slide_ids) ||
                     result.sections_hash !== snapshot.sections_hash ||
                     JSON.stringify(latest) !== JSON.stringify(active())) {
            pendingSnapshot = result; showLiveStatus();
          } else if (pendingSnapshot) { pendingSnapshot = null; showLiveStatus(); }
        } catch (error) {
          if (current === deckId && (error.status === 403 || error.status === 404)) {
            if (dialog.open) { saveDraft(); dialog.close(); }
            ++viewEpoch; ++sequence; deckId = ''; snapshot = null; pendingSnapshot = null; cached = null; button.hidden = true;
          }
        }
      },
    };
  }
  root.StructureReview = {create};
})(window);
