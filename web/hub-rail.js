/* Related artifacts for presentations assigned to a hub project. */
(() => {
  'use strict';
  const header = document.getElementById('artifact-rail-header');
  const items = document.getElementById('artifact-rail-items');
  const picker = document.getElementById('deck-picker');
  const switcher = document.getElementById('deck-switcher');
  const sorterTitle = document.getElementById('sorter-presentation-title');
  const cache = new Map();
  let current = null;
  let request = 0;
  const icon = name => name === 'image' ? '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4h18v16H3zM3 16l5-5 4 4 3-3 6 6M15 8h.01"/></svg>' : window.SlideIcons.svg(name);
  const icons = {presentation:'presentation',html:'code',markdown:'notes',document:'notes',image:'image',svg:'image',file:'archive',other:'archive'};

  /** Restore the original presentation picker when no hub project applies. */
  function fallback() {
    header.hidden = true;
    items.hidden = true;
    switcher.hidden = false;
    sorterTitle.textContent = '';
    sorterTitle.removeAttribute('title');
    document.querySelector('.rail').classList.remove('has-artifact-rail');
  }

  /** Render project navigation without replacing the existing slide controls. */
  function render(deckId, data) {
    const active = data.artifacts.find(item => item.legacy_deck_id === deckId);
    const project = active && data.projects.find(item => item.id === active.project_id);
    if (!project || project.legacy) return fallback();
    const rail = document.querySelector('.rail');
    rail.classList.add('has-artifact-rail');
    switcher.hidden = true;
    sorterTitle.textContent = active.title;
    sorterTitle.title = active.title;
    header.hidden = false;
    items.hidden = false;
    header.replaceChildren();
    items.replaceChildren();
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Choose project');
    for (const entry of data.projects) select.add(new Option(entry.title, entry.id, false, entry.id === project.id));
    select.addEventListener('change', () => { location.href = `/?project=${encodeURIComponent(select.value)}`; });
    header.append(select);
    const related = data.artifacts.filter(item => item.project_id === project.id);
    const ordered = [...related.filter(item => item.id !== active.id), active];
    for (const item of ordered) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'artifact-rail-item';
      button.title = item.title;
      button.innerHTML = icon(icons[item.kind] || 'archive');
      const label = document.createElement('span');
      label.textContent = item.title;
      button.append(label);
      if (item.id === active.id) button.setAttribute('aria-current', 'true');
      else button.addEventListener('click', () => {
        if (item.legacy_deck_id && [...picker.options].some(option => option.value === item.legacy_deck_id)) {
          picker.value = item.legacy_deck_id;
          picker.dispatchEvent(new Event('change', {bubbles:true}));
        } else location.href = `/?artifact=${encodeURIComponent(item.id)}`;
      });
      items.append(button);
    }
  }

  /** Load registry data and discard responses for an older selected deck. */
  async function show(deckId) {
    const previous=current;
    current = deckId;
    const cached=cache.get(deckId);
    if(cached && Date.now()-cached.at<30000){if(previous!==deckId)render(deckId,cached.data);return;}
    const serial = ++request;
    if (cached) render(deckId, cached.data);
    try {
      const response = await fetch('/api/hub');
      if (!response.ok) throw new Error('Registry unavailable');
      const data = await response.json();
      cache.set(deckId, {data,at:Date.now()});
      if (current === deckId && serial === request) render(deckId, data);
    } catch {
      if (current === deckId && serial === request && !cache.has(deckId)) fallback();
    }
  }
  window.ArtifactRail = {show};
})();
