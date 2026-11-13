/* Show human viewers of this deck without changing the slide previews. */
((root) => {
  'use strict';
  function create({button, panel, rail, session}) {
    const tab = Array.from(root.crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
    const workspace = encodeURIComponent(session.workspace);
    const endpoint = deck => `/api/workspaces/${workspace}/artifacts/${encodeURIComponent(deck)}/presence`;
    let target = null, viewers = [], listening = false, pending = false, pageActive = true;

    function badge(text, label) {
      const mark = document.createElement('span');
      mark.className = 'presence-badge';
      mark.textContent = text;
      mark.title = label;
      mark.setAttribute('aria-hidden', 'true');
      return mark;
    }
    function badges(people, limit) {
      const marks = people.slice(0, limit).map(({name}) => badge(
        name.split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join('').toUpperCase(), name,
      ));
      if (people.length > limit) marks.push(badge(`+${people.length - limit}`, people.slice(limit).map(({name}) => name).join(', ')));
      return marks;
    }
    function render() {
      if (!viewers.length && button.hidden) return;
      const bySlide = new Map();
      for (const viewer of viewers) {
        if (!bySlide.has(viewer.slide_id)) bySlide.set(viewer.slide_id, []);
        bySlide.get(viewer.slide_id).push(viewer);
      }
      const locations = new Map();
      rail.querySelectorAll('.slide-thumb[data-id]').forEach((thumb, index) => {
        const id = thumb.dataset.id, people = bySlide.get(id) || [];
        locations.set(id, `Slide ${index + 1}`);
        const existing = thumb.querySelector('.slide-presence-markers');
        const names = people.map(({name}) => name).join(', ');
        if (names && existing?.dataset.names !== names) {
          const markers = existing || document.createElement('span');
          markers.className = 'slide-presence-markers';
          markers.dataset.names = names;
          markers.replaceChildren(...badges(people, 1));
          thumb.querySelector('.thumb-caption').insertBefore(markers, thumb.querySelector('.dot'));
          thumb.setAttribute('aria-description', `Viewing this slide: ${names}`);
        } else if (!names && existing) {
          existing.remove();
          thumb.removeAttribute('aria-description');
        }
      });
      button.hidden = !viewers.length;
      if (!viewers.length && panel.matches(':popover-open')) panel.hidePopover();
      button.setAttribute('aria-label', `People in this deck (${viewers.length})`);
      button.replaceChildren(...badges(viewers, 2));
      panel.replaceChildren(...viewers.map(({name, slide_id: id}) => {
        const row = document.createElement('li');
        const person = document.createElement('span');
        person.textContent = name;
        const location = document.createElement('small');
        location.textContent = locations.get(id) || '';
        row.append(person, location);
        return row;
      }));
    }
    function clear() {
      if (viewers.length) { viewers = []; render(); }
    }
    function post(deck, slide, options) {
      return root.fetch(endpoint(deck), {
        ...options, method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tab, slide_id: slide}),
      });
    }
    function leave(deck = target?.deck) {
      if (listening && deck) void post(deck, null, {keepalive: true}).catch(() => {});
      clear();
    }
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !panel.matches(':popover-open')) return;
      event.preventDefault();
      panel.hidePopover();
      button.focus();
    }, true);
    async function refresh() {
      if (!listening || !target || pending || document.hidden || !pageActive) return;
      const current = target;
      pending = true;
      try {
        const response = session.observe(await post(current.deck, current.slide, {signal: AbortSignal.timeout(8000)}));
        if (!response.ok || response.redirected) throw new Error('Presence unavailable');
        const next = await response.json();
        if (target === current && listening && pageActive && !document.hidden && JSON.stringify(next) !== JSON.stringify(viewers)) {
          viewers = next;
          render();
        }
      } catch {
        if (target === current) clear();
      } finally {
        pending = false;
        if (current.deck !== target?.deck || document.hidden || !pageActive) leave(current.deck);
        if (target !== current) void refresh();
      }
    }
    void session.get().then(info => {
      if (info.collaboration_url !== '/together') return;
      listening = true;
      const timer = root.setInterval(refresh, 5000);
      document.addEventListener('visibilitychange', () => { if (document.hidden) leave(); else void refresh(); });
      root.addEventListener('pagehide', () => { pageActive = false; leave(); });
      root.addEventListener('pageshow', () => { pageActive = true; void refresh(); });
      root.addEventListener('artifact-session-expired', () => { listening = false; root.clearInterval(timer); clear(); }, {once: true});
      void refresh();
    }).catch(() => {});
    return {
      select(deck, slide) {
        if (target?.deck === deck && target?.slide === slide) return;
        if (target && target.deck !== deck) leave(target.deck);
        target = deck ? {deck, slide} : null;
        void refresh();
      },
      render,
    };
  }
  root.StudioDeckPresence = {create};
})(window);
