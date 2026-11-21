/* Human review of the visual direction of an exact presentation delivery. */
window.StudioDeliveryReview = {
  create({api, current, canReview, updatePending, toast}) {
    const dialog = document.getElementById('delivery-review');
    const button = document.getElementById('delivery-review-button');
    const find = selector => dialog.querySelector(selector);
    const form = find('form');
    const submit = find('button[type=submit]');
    const status = find('[data-review-status]');
    const error = find('[data-review-error]');
    const history = find('details');
    const retry = find('[data-review-reload]');
    const stages = find('[data-review-stage]');
    const firstDirection = {unrated:'Unrated', accepted:'Accepted', redirect:'Change requested'};
    let deckId, state, generation = 0, sending = false, requestId;
    window.SlideIcons.button(button, 'palette', 'Review visual direction', true);
    window.SlideIcons.button(find('[data-review-close]'), 'close', 'Close');
    const path = () => '/api/decks/' + encodeURIComponent(deckId) + '/delivery-reviews';
    const isCurrent = version => dialog.open && version === generation && deckId === current()?.id;
    const outdated = () => updatePending() || state?.delivery_hash !== current()?.delivery_hash;

    function alertUser(message) {
      error.textContent = message;
      error.hidden = !message;
    }

    function choices() {
      const values = new FormData(form);
      return {
        stage: state.current_review?.stage || (stages.hidden ? 'follow_up' : values.get('stage')),
        direction: values.get('direction') || 'unrated',
        factual: values.has('factual'),
        legibility: values.has('legibility'),
      };
    }

    function updateSubmit() {
      const draft = choices();
      find('[data-review-details-hint]').hidden = !(draft.direction === 'redirect' || draft.factual || draft.legibility);
      submit.disabled = sending || !draft.stage || !(draft.direction !== 'unrated' || draft.factual || draft.legibility);
    }

    function verdict(review) {
      const recorded = [];
      if (review.direction !== 'unrated') recorded.push(firstDirection[review.direction].toLowerCase());
      if (review.factual) recorded.push('factual corrections');
      if (review.legibility) recorded.push('layout or legibility corrections');
      return recorded.join(' · ');
    }

    function showResult(result) {
      state = result;
      const review = state.personal_review;
      for (const value of history.querySelectorAll('[data-review-value]')) {
        const key = value.dataset.reviewValue;
        value.textContent = key === 'first_delivery' ? firstDirection[state[key]] : state[key];
      }
      history.hidden = false;
      history.open = !!review || !canReview();
      stages.hidden = state.previous_review_count > 0;
      form.reset();
      form.hidden = !!review || !canReview() || outdated();
      requestId = '';
      updateSubmit();

      if (outdated()) {
        status.textContent = 'The presentation has changed. Open the latest version before recording a review.';
      } else if (review) {
        const label = review.stage === 'first' ? 'Your first-draft review' : 'Your review of this revision';
        status.textContent = label + ' · ' + verdict(review) + (canReview() ? '. You can review the next version after it changes.' : '.');
      } else if (!canReview()) {
        status.textContent = state.current_review ? 'Team review of this delivery · ' + verdict(state.current_review) : 'This delivery has no recorded review.';
      } else {
        status.textContent = stages.hidden ? (state.current_review?.stage === 'first' ? 'Reviewing the first draft.' : 'Reviewing a later revision.') : '';
      }
    }

    async function load() {
      const version = ++generation;
      state = null;
      sending = false;
      form.inert = false;
      alertUser('');
      retry.hidden = history.hidden = form.hidden = true;
      status.textContent = 'Loading review…';
      try {
        const result = await api(path(), undefined, {cache:'no-store'});
        if (isCurrent(version)) showResult(result);
      } catch (failure) {
        if (!isCurrent(version)) return;
        status.textContent = '';
        alertUser(failure.message);
        retry.hidden = false;
      }
    }

    button.addEventListener('click', () => {
      const deck = current();
      if (!deck) return;
      if (updatePending()) {
        toast('Open the latest presentation update before reviewing its visual direction.');
        return;
      }
      const menu = document.getElementById('workspace-menu');
      if (menu.matches(':popover-open')) menu.hidePopover();
      deckId = deck.id;
      find('[data-review-deck]').textContent = deck.title;
      dialog.showModal();
      void load();
    });
    find('[data-review-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
      ++generation;
      document.getElementById('workspace-menu-button').focus();
    });
    retry.addEventListener('click', load);
    form.addEventListener('change', () => {
      requestId = '';
      alertUser('');
      updateSubmit();
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (submit.disabled || !state || !canReview()) return;
      if (outdated()) { showResult(state); return; }
      const version = generation;
      const draft = choices();
      sending = true;
      requestId ||= crypto.randomUUID();
      form.inert = true;
      updateSubmit();
      alertUser('');
      try {
        const result = await api(path(), {...draft, delivery_hash:state.delivery_hash, request_id:requestId});
        if (isCurrent(version)) {
          showResult(result);
          history.querySelector('summary').focus();
        }
      } catch (failure) {
        if (!isCurrent(version)) return;
        alertUser(failure.message);
        if ([403, 404, 409].includes(failure.status)) {
          form.hidden = true;
          retry.hidden = false;
          retry.focus();
        }
      } finally {
        if (isCurrent(version)) {
          sending = false;
          form.inert = false;
          updateSubmit();
        }
      }
    });
    return {
      sync(id) {
        button.disabled = !id;
        if (dialog.open && deckId !== id) dialog.close();
        if (dialog.open && state && !sending && !form.hidden && (!canReview() || outdated())) showResult(state);
      },
    };
  },
};
