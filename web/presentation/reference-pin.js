/* A human chooses the saved slide version to use as a design reference. */
((root) => {
  'use strict';
  function create(dialog, {api, iconButton}) {
    const doc = dialog.ownerDocument;
    const scopes = dialog.querySelector('[data-reference-scopes]');
    const priority = dialog.querySelector('[data-reference-priority]');
    const status = dialog.querySelector('[data-reference-status]');
    const reload = dialog.querySelector('[data-reload]');
    const close = dialog.querySelector('[data-close]');
    const names = {deck: 'presentation', project: 'project'};
    let target = null;
    iconButton(close, 'close', 'Close design reference');
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      if (dialog.open) return;
      target = null;
      if (doc.activeElement === doc.body) doc.getElementById('workspace-menu-button')?.focus();
    });

    function action(scope, verb, pin, clear = false) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.dataset.scope = scope;
      button.dataset.expectedPin = pin?.pin_id || '';
      button.dataset.operation = clear ? 'clear' : 'pin';
      const label = verb + ' ' + names[scope] + ' reference';
      button.setAttribute('aria-label', label);
      button.textContent = verb;
      return button;
    }

    function render(state, focusScope) {
      priority.hidden = !state.project.editable && !state.project.pin;
      scopes.replaceChildren();
      for (const scope of ['deck', 'project']) {
        const {editable, pin} = state[scope];
        if (!editable && !pin) continue;
        const row = doc.createElement('div');
        row.className = 'reference-pin-row';
        const name = 'This ' + names[scope];
        row.setAttribute('role', 'group');
        row.setAttribute('aria-label', name);
        row.innerHTML = root.SlideIcons.svg(scope === 'deck' ? 'presentation' : 'project') +
          '<div class="reference-pin-copy"><strong></strong><span class="reference-pin-summary"></span></div>' +
          '<div class="reference-pin-actions"></div>';
        row.querySelector('strong').textContent = name;
        const sameSlide = pin?.deck_id === target.deckId && pin.slide_id === target.slideId;
        const sameVersion = sameSlide && pin.content_hash === target.contentHash;
        const summary = row.querySelector('.reference-pin-summary');
        summary.textContent = pin ? 'Pinned · ' + pin.title : 'No slide pinned';
        if (sameSlide && !sameVersion) summary.append(' (earlier version)');
        const actions = row.querySelector('.reference-pin-actions');
        if (editable) {
          if (!sameVersion) actions.append(action(scope, !pin ? 'Pin' : sameSlide ? 'Update' : 'Replace', pin));
          if (pin) {
            const remove = action(scope, 'Remove', pin, true);
            if (!sameVersion) iconButton(remove, 'close', 'Remove ' + names[scope] + ' reference');
            actions.append(remove);
          }
        }
        scopes.append(row);
        if (focusScope === scope) actions.querySelector('button')?.focus();
      }
    }

    async function loadOrChange(button) {
      const request = target;
      if (!request || !dialog.open) return;
      const scope = button?.dataset.scope;
      const body = scope ? {scope, expected_pin_id: button.dataset.expectedPin} : undefined;
      if (body && button.dataset.operation === 'pin') {
        body.slide_id = request.slideId;
        body.content_hash = request.contentHash;
      }
      reload.hidden = true;
      for (const item of scopes.querySelectorAll('button')) item.disabled = true;
      status.textContent = scope ? 'Saving reference…' : 'Loading references…';
      try {
        const state = await api('/api/decks/' + encodeURIComponent(request.deckId) + '/design-reference', body, {cache: 'no-store'});
        if (target !== request || !dialog.open) return;
        render(state, scope);
        status.textContent = scope ? (button.dataset.operation === 'clear' ? 'Reference removed.' : 'Reference saved.') : '';
      } catch (error) {
        if (target !== request || !dialog.open) return;
        status.textContent = error.message || 'Could not load the references.';
        reload.hidden = false;
      }
    }
    reload.addEventListener('click', () => loadOrChange());
    scopes.addEventListener('click', event => {
      const button = event.target.closest('button[data-scope]');
      if (button && !button.disabled) void loadOrChange(button);
    });
    function open(slide) {
      target = slide;
      priority.hidden = true;
      scopes.replaceChildren();
      dialog.querySelector('[data-slide-title]').textContent = slide.title;
      dialog.showModal();
      void loadOrChange();
    }
    return {open};
  }
  root.PresentationReferencePin = {create};
})(window);
