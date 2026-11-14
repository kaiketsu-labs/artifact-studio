/* Show viewers and shared agent activity without changing slide previews. */
((root) => {
  'use strict';
  function create({button, panel, rail, session}) {
    const tab = Array.from(root.crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
    const workspace = encodeURIComponent(session.workspace);
    const endpoint = deck => `/api/workspaces/${workspace}/artifacts/${encodeURIComponent(deck)}/presence`;
    let target = null, viewers = [], agents = [], listening = false, pending = false, pageActive = true;
    let lastSuccess = 0;
    const staleAfter = 45000;
    const compact = root.matchMedia('(max-width: 600px)');

    function badge(text, label, hue) {
      const mark = document.createElement('span');
      mark.className = 'presence-badge';
      if (text === null) {
        mark.classList.add('presence-agent');
        mark.innerHTML = root.SlideIcons.svg('robot');
      } else mark.textContent = text;
      if (hue !== undefined) mark.style.setProperty('--presence-hue', hue);
      mark.title = label;
      mark.setAttribute('aria-hidden', 'true');
      return mark;
    }
    function personBadge(name) {
      const initials = name.split(/\s+/).slice(0, 2).map(part => Array.from(part)[0]).join('').toUpperCase();
      const hue = Array.from(name).reduce((value, char) => (value * 31 + char.codePointAt(0)) % 360, 0);
      return badge(initials, name, hue);
    }
    function badges(people, limit) {
      const humans = people.filter(person => !person.agent);
      const marks = humans.slice(0, limit).map(({name}) => personBadge(name));
      if (humans.length > limit) marks.push(badge(`+${humans.length - limit}`, humans.slice(limit).map(({name}) => name).join(', ')));
      if (people.some(person => person.agent)) marks.push(badge(null, 'Agent working'));
      return marks;
    }
    function render() {
      if (!viewers.length && !agents.length && button.hidden) return;
      const bySlide = new Map();
      for (const viewer of viewers) {
        if (!bySlide.has(viewer.slide_id)) bySlide.set(viewer.slide_id, []);
        bySlide.get(viewer.slide_id).push(viewer);
      }
      const locations = new Map();
      rail.querySelectorAll('.slide-thumb[data-id]').forEach((thumb, index) => {
        const id = thumb.dataset.id, people = bySlide.get(id) || [];
        locations.set(id, {thumb, number: index + 1});
        const existing = thumb.querySelector('.slide-presence-markers');
        const names = people.filter(person => !person.agent).map(({name}) => name).join(', ');
        const agent = people.some(person => person.agent);
        const description = [names && `Viewing this slide: ${names}`, agent && 'Agent working on this slide'].filter(Boolean).join('. ');
        if (description && existing?.dataset.description !== description) {
          const markers = existing || document.createElement('span');
          markers.className = 'slide-presence-markers';
          markers.dataset.description = description;
          markers.title = description;
          markers.replaceChildren(...badges(people, 2));
          thumb.querySelector('.thumb-preview').append(markers);
          thumb.setAttribute('aria-description', description);
        } else if (!description && existing) {
          existing.remove();
          thumb.removeAttribute('aria-description');
        }
      });
      button.hidden = !viewers.length && !agents.length;
      if (button.hidden && panel.matches(':popover-open')) panel.hidePopover();
      const people = viewers.filter(person => !person.agent).length;
      const working = viewers.some(person => person.agent);
      const agentLabel = agents.length && `${agents.length} ${agents.length === 1 ? 'agent' : 'agents'} listening`;
      button.setAttribute('aria-label', [people && `People in this deck (${people})`, agentLabel || working && 'Agent activity'].filter(Boolean).join(', '));
      button.title = button.getAttribute('aria-label');
      panel.setAttribute('aria-label', working || agents.length ? 'Deck activity' : 'People in this deck');
      const marks = badges(viewers, compact.matches ? 1 : 3);
      if (agents.length) {
        const mark = marks.find(item => item.classList.contains('presence-agent')) || badge(null, agentLabel);
        mark.append(String(agents.length));
        mark.title = agentLabel;
        if (!marks.includes(mark)) marks.push(mark);
      }
      button.replaceChildren(...marks);
      const focused = panel.contains(document.activeElement) ? document.activeElement.dataset.presenceRow : null;
      panel.replaceChildren(...viewers.map(({name, slide_id: id, agent}) => {
        const row = document.createElement('li');
        const location = locations.get(id);
        const item = document.createElement(location ? 'button' : 'div');
        item.className = 'presence-person';
        if (location) {
          item.type = 'button';
          item.dataset.presenceRow = `${agent ? 'agent' : name}:${id}`;
          item.addEventListener('click', () => {
            panel.hidePopover();
            const focus = location.thumb.getClientRects().length ? location.thumb : button;
            focus.focus({preventScroll: true});
            location.thumb.click();
          });
        }
        const person = document.createElement('span');
        person.textContent = name;
        const slide = document.createElement('small');
        slide.textContent = location ? `Slide ${location.number}` : '';
        item.append(agent ? badge(null, name) : personBadge(name), person, slide);
        row.append(item);
        return row;
      }), ...agents.map(name => {
        const row = document.createElement('li');
        const item = document.createElement('div');
        item.className = 'presence-person';
        const label = document.createElement('span');
        label.textContent = name;
        const status = document.createElement('small');
        status.textContent = 'Listening';
        item.append(badge(null, 'Agent'), label, status);
        row.append(item);
        return row;
      }));
      if (focused && panel.matches(':popover-open')) {
        const next = Array.from(panel.querySelectorAll('button')).find(item => item.dataset.presenceRow === focused);
        (next || button).focus({preventScroll: true});
      }
    }
    function clear() {
      if (viewers.length || agents.length) { viewers = []; agents = []; render(); }
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
    compact.addEventListener('change', render);
    async function refresh() {
      if (!listening || !target || pending || document.hidden || !pageActive) return;
      const current = target;
      pending = true;
      try {
        const response = session.observe(await post(current.deck, current.slide, {signal: AbortSignal.timeout(8000)}));
        if (target === current && [401, 403, 404].includes(response.status)) clear();
        if (!response.ok || response.redirected) throw new Error('Presence unavailable');
        const next = await response.json();
        if (target === current && listening && pageActive) {
          lastSuccess = Date.now();
          if (JSON.stringify(next) !== JSON.stringify(viewers)) {
            viewers = next;
            render();
          }
        }
      } catch {
        if (target === current && Date.now() - lastSuccess >= staleAfter) clear();
      } finally {
        pending = false;
        if (current.deck !== target?.deck || !pageActive) {
          void post(current.deck, null, {keepalive: true}).catch(() => {});
        }
        if (target !== current) void refresh();
      }
    }
    void session.get().then(info => {
      if (info.collaboration_url !== '/together') return;
      listening = true;
      const timer = root.setInterval(refresh, 5000);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
      root.addEventListener('pagehide', () => { pageActive = false; leave(); });
      root.addEventListener('pageshow', () => { pageActive = true; void refresh(); });
      root.addEventListener('artifact-session-expired', () => { listening = false; root.clearInterval(timer); clear(); }, {once: true});
      void refresh();
    }).catch(() => {});
    return {
      setAgents(deck, scopedAgents) {
        if (target?.deck !== deck) return;
        // A recent tool call could be for a different artifact. Only a deck lease counts here.
        const next = scopedAgents.filter(agent => !agent.mine && agent.status === 'listening').map(agent => agent.name).sort();
        if (JSON.stringify(next) === JSON.stringify(agents)) return;
        agents = next;
        render();
      },
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
