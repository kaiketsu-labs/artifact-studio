/* Fictional, in-memory motion study. It does not call any application endpoints. */
(() => {
  'use strict';
  const doc = document, theme = doc.getElementById('theme-toggle'), replay = doc.getElementById('replay');
  const composer = doc.getElementById('composer'), input = doc.getElementById('message'), slot = doc.getElementById('draft-reference');
  const undoRow = doc.getElementById('undo-row'), status = doc.getElementById('status'), thread = doc.getElementById('thread');
  const controllers = new Set();
  let lastInput = 'mouse', replayEpoch = 0, replayTimers = [], referencePresent = true;

  function installReference(container) {
    const card = container.querySelector('.reference-card'), title = card.querySelector('.reference-title'), visual = card.querySelector('.reference-visual');
    let hover = false, keyboard = false, touchOpen = false, dismissed = false, replayOpen = false, enterTimer = null, leaveTimer = null;
    const isOpen = () => card.dataset.open === 'true';
    function paint() {
      const open = (hover || keyboard || touchOpen || replayOpen) && !dismissed && !container.hidden;
      card.dataset.open = String(open); title.setAttribute('aria-expanded', String(open)); visual.setAttribute('aria-hidden', String(!open));
    }
    function fold() {
      clearTimeout(enterTimer); clearTimeout(leaveTimer); touchOpen = false; replayOpen = false; dismissed = true; paint();
    }
    card.addEventListener('pointerenter', event => {
      if (event.pointerType === 'touch') return;
      clearTimeout(leaveTimer); dismissed = false; hover = true;
      enterTimer = setTimeout(paint, 70);
    });
    card.addEventListener('pointerleave', event => {
      if (event.pointerType === 'touch') return;
      clearTimeout(enterTimer); hover = false;
      leaveTimer = setTimeout(() => { dismissed = false; paint(); }, 80);
    });
    card.addEventListener('focusin', () => {
      if (lastInput !== 'keyboard') return;
      keyboard = true; dismissed = false; paint();
    });
    card.addEventListener('focusout', event => {
      if (card.contains(event.relatedTarget)) return;
      keyboard = false; dismissed = false; paint();
    });
    title.addEventListener('click', event => {
      if (lastInput !== 'touch' && event.detail !== 0) return;
      const next = !isOpen(); touchOpen = next; dismissed = !next; paint();
    });
    card.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); fold();
    });
    const controller = {container, card, title, fold, replay(value) {replayOpen = value; dismissed = false; paint();}};
    controllers.add(controller);
    return controller;
  }
  const draft = installReference(slot);
  doc.addEventListener('pointerdown', event => {
    lastInput = event.pointerType === 'touch' ? 'touch' : 'mouse';
    if (event.pointerType === 'touch') for (const item of controllers) if (!item.card.contains(event.target)) item.fold();
  }, true);
  doc.addEventListener('keydown', event => {if (['Tab','Enter',' '].includes(event.key)) lastInput = 'keyboard';}, true);
  theme.addEventListener('click', () => {
    const next = doc.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    doc.documentElement.dataset.theme = next;
    theme.setAttribute('aria-label', `Switch to ${next === 'dark' ? 'light' : 'dark'} appearance`);
    theme.querySelector('use').setAttribute('href', `#icon-${next === 'dark' ? 'sun' : 'moon'}`);
  });
  function cancelReplay() {
    replayEpoch++; replayTimers.forEach(clearTimeout); replayTimers = [];
    for (const item of controllers) item.replay(false);
    replay.setAttribute('aria-busy', 'false');
  }
  replay.addEventListener('click', () => {
    cancelReplay();
    if (!referencePresent) restore();
    const epoch = replayEpoch;
    replay.setAttribute('aria-busy', 'true'); draft.fold();
    replayTimers.push(setTimeout(() => {if (epoch === replayEpoch) draft.replay(true);}, 220));
    replayTimers.push(setTimeout(() => {if (epoch === replayEpoch) {draft.replay(false); replay.setAttribute('aria-busy', 'false');}}, 1620));
  });
  slot.querySelector('.reference-remove').addEventListener('click', () => {
    cancelReplay(); draft.fold(); referencePresent = false; slot.hidden = true; undoRow.hidden = false;
    status.textContent = 'Reference removed. Your message is unchanged.'; doc.getElementById('undo').focus();
  });
  function restore() {
    referencePresent = true; slot.hidden = false; undoRow.hidden = true; draft.fold(); status.textContent = 'Reference restored.';
  }
  doc.getElementById('undo').addEventListener('click', () => {restore(); draft.title.focus();});
  composer.addEventListener('submit', event => {
    event.preventDefault(); cancelReplay();
    if (!input.value.trim() && !referencePresent) return;
    const message = doc.createElement('article'); message.className = 'sent-message';
    const text = doc.createElement('p'); text.textContent = input.value.trim() || 'Reference for the next version.'; message.append(text);
    if (referencePresent) {
      const saved = slot.cloneNode(true); saved.removeAttribute('id'); saved.hidden = false;
      const id = `saved-preview-${controllers.size}`;
      saved.querySelector('.reference-visual').id = id; saved.querySelector('.reference-title').setAttribute('aria-controls', id);
      saved.querySelector('.reference-remove').remove(); saved.querySelector('.reference-card').dataset.open = 'false';
      saved.querySelector('.reference-visual').setAttribute('aria-hidden', 'true'); saved.querySelector('.reference-title').setAttribute('aria-expanded', 'false');
      message.append(saved); installReference(saved);
    }
    doc.getElementById('hover-hint').hidden = true; thread.append(message); input.value = '';
    referencePresent = false; slot.hidden = true; draft.fold(); undoRow.hidden = false;
    status.textContent = 'Demo message sent. Hover its saved reference to preview.'; input.focus();
  });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function motionNote() {doc.getElementById('motion-note').textContent = reduced.matches ? 'Reduced motion: instant open and close.' : 'Smooth opening. Quieter closing.';}
  reduced.addEventListener('change', motionNote); motionNote();
  doc.addEventListener('visibilitychange', () => {if (doc.hidden) {cancelReplay(); for (const item of controllers) item.fold();}});
})();
