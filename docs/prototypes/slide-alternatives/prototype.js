/* Fictional, in-memory interaction draft. It never calls product APIs. */
(() => {
  'use strict';
  const doc = document;
  const $ = id => doc.getElementById(id);
  const extraIcons = {
    designs: '<path d="M7 7h14v14H7zM3 17V3h14M11 11h6v6h-6z"/>',
    compare: '<path d="M3 5h7v14H3zM14 5h7v14h-7zM12 3v18"/>',
    layers: '<path d="m12 3 10 6-10 6L2 9l10-6Zm-9 9 9 6 9-6M3 16l9 6 9-6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    grid: '<path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/>',
    fullscreen: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M5 22v-3a7 7 0 0 1 14 0v3"/>',
    robot: '<rect x="4" y="7" width="16" height="14" rx="4"/><path d="M12 3v4m-2-4h4M1 12v5m22-5v5M8 12h.01M16 12h.01M9 17h6"/>',
  };
  const icon = name => extraIcons[name] ? `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${extraIcons[name]}</svg>` : window.SlideIcons.svg(name);
  doc.querySelectorAll('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });
  const foot = '<footer class="slide-footer"><span>Artifact Studio / A clearer way to work</span><span>02</span></footer>';
  const original = `<article class="slide-art original"><p class="slide-eyebrow">From first thought to final slide</p><h2>Good slides happen<br>in the loop.</h2><div class="original-cards"><div><small>01</small><strong>Brief</strong><p>Bring your audience,<br>ideas and references.</p></div><div><small>02</small><strong>Draft</strong><p>Let your agent turn<br>the story into slides.</p></div><div><small>03</small><strong>Review</strong><p>Point to the change.<br>Make it better, together.</p></div></div>${foot}</article>`;
  const editorial = `<article class="slide-art editorial"><p class="slide-eyebrow">From first thought to final slide</p><div class="editorial-layout"><div><h2>Good slides<br>happen<br><em>in the loop.</em></h2></div><div class="editorial-steps"><div class="editorial-step"><small>01</small><div><strong>Brief</strong><p>Bring your audience,<br>ideas and references.</p></div></div><div class="editorial-step"><small>02</small><div><strong>Draft</strong><p>Let your agent turn<br>the story into slides.</p></div></div><div class="editorial-step"><small>03</small><div><strong>Review</strong><p>Point to the change.<br>Make it better, together.</p></div></div></div></div>${foot}</article>`;
  const bold = `<article class="slide-art bold"><p class="slide-eyebrow">From first thought to final slide</p><h2>Good slides happen<br><em>in the loop.</em></h2><div class="bold-steps"><div class="bold-step"><small>01</small><div><strong>Brief</strong><p>Bring your audience,<br>ideas and references.</p></div></div><div class="bold-step"><small>02</small><div><strong>Draft</strong><p>Let your agent turn<br>the story into slides.</p></div></div><div class="bold-step"><small>03</small><div><strong>Review</strong><p>Point to the change.<br>Make it better, together.</p></div></div></div>${foot}</article>`;
  const diagram = `<article class="slide-art diagram"><p class="slide-eyebrow">From first thought to final slide</p><h2>Good slides happen<br>in the loop.</h2><div class="diagram-flow"><div class="diagram-node"><small>01</small><strong>Brief</strong><p>Bring your audience,<br>ideas and references.</p></div><span class="diagram-arrow">→</span><div class="diagram-node"><small>02</small><strong>Draft</strong><p>Let your agent turn<br>the story into slides.</p></div><span class="diagram-arrow">→</span><div class="diagram-node"><small>03</small><strong>Review</strong><p>Point to the change.<br>Make it better, together.</p></div></div><div class="diagram-return">Refine, then repeat ↵</div>${foot}</article>`;
  const designs = [
    {id:'original', name:'Original', letter:'', html:original},
    {id:'editorial', name:'Editorial', letter:'A', html:editorial},
    {id:'bold', name:'Bold', letter:'B', html:bold},
    {id:'diagram', name:'Diagram', letter:'C', html:diagram},
  ];
  const design = id => designs.find(item => item.id === id);
  const otherSlide = (title, number, cover = false) => `<article class="slide-art ${cover ? 'cover' : 'simple'}"><p class="slide-eyebrow">A clearer way to work</p><h2>${title}</h2>${cover ? '' : '<div class="simple-blocks"><span></span><span></span></div>'}<footer class="slide-footer"><span>Artifact Studio</span><span>0${number}</span></footer></article>`;
  const slides = [
    {name:'A clearer way to work', html:otherSlide('A clearer<br>way to work.',1,true)},
    {name:'Good slides happen in the loop', html:original},
    {name:'One slide. One clear idea.', html:otherSlide('One slide.<br>One clear idea.',3)},
    {name:'Let the story move', html:otherSlide('Let the<br>story move.',4)},
    {name:'Ready for the room', html:otherSlide('Ready for<br>the room.',5,true)},
    {name:'Space for the details', html:otherSlide('Space for<br>the details.',6)},
  ];
  let current = 'original', selected = 'original', activeSlide = 1, previousChoice = null, approved = false;
  let compareTrigger = null, compareReference = current;
  const peek = $('alternative-peek');
  let peekTrigger = null, peekCloseTimer = null, suppressPeekFocus = false;
  const announce = text => { $('live-status').textContent = text; };
  const selectedIsCurrent = () => selected === current;

  function renderRail() {
    closeAlternativePeek();
    $('slide-overview').innerHTML = slides.map((slide,index) => `${index === 0 || index === 3 ? `<div class="draft-section-label">${icon('down')}${index === 0 ? 'The story' : 'In the room'}<span>3</span></div>` : ''}<div class="draft-slide-entry"><button class="draft-slide-thumb" data-slide="${index}" aria-label="Slide ${index+1}: ${slide.name}${index === 1 ? ', 3 alternative designs available' : ''}" ${index === activeSlide ? 'aria-current="true"' : ''}><div class="rail-slide-image" aria-hidden="true">${index === 1 ? design(current).html : slide.html}</div><span class="rail-slide-caption"><span class="rail-slide-number">0${index+1}</span><span>${slide.name}</span><span class="dot ${index === 0 || index === 2 ? 'green' : 'red'}"></span></span></button>${index === 1 ? `<button class="rail-alternatives" data-alternatives aria-label="View 3 alternative designs for slide 2" aria-expanded="false" aria-controls="alternative-peek" aria-describedby="alternative-peek-help">${icon('designs')}<span>3</span></button>` : ''}</div>`).join('');
  }

  function closeAlternativePeek({restoreFocus = false} = {}) {
    clearTimeout(peekCloseTimer);
    if (peek.matches(':popover-open')) peek.hidePopover();
    peekTrigger?.setAttribute('aria-expanded','false');
    if (restoreFocus && peekTrigger?.isConnected) { suppressPeekFocus = true; peekTrigger.focus({preventScroll:true}); suppressPeekFocus = false; }
  }

  function positionAlternativePeek(point) {
    const width = peek.offsetWidth, height = peek.offsetHeight;
    let top = point.y-height-16;
    if (top < 12) top = point.y+18;
    peek.style.left = `${Math.max(16,Math.min(innerWidth-width-16,point.x-35))}px`;
    peek.style.top = `${Math.max(16,Math.min(innerHeight-height-16,top))}px`;
  }

  function openAlternativePeek(trigger, point) {
    clearTimeout(peekCloseTimer);
    peekTrigger = trigger;
    if (!peek.matches(':popover-open')) {
      $('peek-fan').innerHTML = designs.filter(item => item.id !== current).map(item => `<span class="peek-slot"><button class="peek-card" data-peek-design="${item.id}" aria-label="Preview ${item.name} design"><span class="peek-card-image" aria-hidden="true">${item.html}</span><span class="peek-card-name">${item.name}</span></button></span>`).join('');
      peek.showPopover();
      trigger.setAttribute('aria-expanded','true');
    }
    const rect = trigger.getBoundingClientRect();
    positionAlternativePeek(point || {x:rect.left+rect.width/2,y:rect.top});
  }

  function queuePeekClose() {
    clearTimeout(peekCloseTimer);
    peekCloseTimer = setTimeout(() => {
      if (!peek.matches(':hover') && !peekTrigger?.matches(':hover') && !peek.contains(doc.activeElement) && doc.activeElement !== peekTrigger) closeAlternativePeek();
    },240);
  }

  function openDesignOptions(id) {
    closeAlternativePeek();
    if (activeSlide !== 1) { activeSlide = 1; selected = current; renderSlide(); }
    $('design-tray').hidden = false; $('design-toggle').setAttribute('aria-expanded','true');
    doc.body.classList.remove('slides-open'); $('toggle-slides').setAttribute('aria-expanded','false');
    preview(id || (selected !== current ? selected : designs.find(item => item.id !== current).id),{focus:true});
    $('design-tray').scrollIntoView({block:'nearest'});
  }

  $('slide-overview').addEventListener('pointerover', event => { const trigger = event.target.closest('[data-alternatives]'); if (trigger && event.pointerType !== 'touch' && !trigger.contains(event.relatedTarget)) openAlternativePeek(trigger,{x:event.clientX,y:event.clientY}); });
  $('slide-overview').addEventListener('pointermove', event => { if (event.target.closest('[data-alternatives]') && peek.matches(':popover-open')) positionAlternativePeek({x:event.clientX,y:event.clientY}); });
  $('slide-overview').addEventListener('pointerout', event => { const trigger = event.target.closest('[data-alternatives]'); if (trigger && !trigger.contains(event.relatedTarget)) queuePeekClose(); });
  $('slide-overview').addEventListener('focusin', event => { const trigger = event.target.closest('[data-alternatives]'); if (trigger && !suppressPeekFocus && trigger.matches(':focus-visible')) openAlternativePeek(trigger); });
  $('slide-overview').addEventListener('focusout', queuePeekClose);
  $('slide-overview').addEventListener('keydown', event => { if (event.key === 'ArrowDown' && event.target.closest('[data-alternatives]')) { event.preventDefault(); openAlternativePeek(event.target.closest('[data-alternatives]')); peek.querySelector('[data-peek-design]').focus(); } });
  peek.addEventListener('pointerenter', () => clearTimeout(peekCloseTimer));
  peek.addEventListener('pointerleave', queuePeekClose);
  peek.addEventListener('focusout', queuePeekClose);
  peek.addEventListener('click', event => { const option = event.target.closest('[data-peek-design]'); if (option) openDesignOptions(option.dataset.peekDesign); });
  $('peek-all').addEventListener('click', () => openDesignOptions());
  doc.addEventListener('pointerdown', event => { if (!peek.contains(event.target) && !event.target.closest('[data-alternatives]')) closeAlternativePeek(); });
  doc.addEventListener('keydown', event => { if (event.key === 'Escape' && peek.matches(':popover-open')) { event.preventDefault(); event.stopImmediatePropagation(); closeAlternativePeek({restoreFocus:peek.contains(doc.activeElement) || doc.activeElement === peekTrigger}); } },true);
  doc.addEventListener('scroll', () => closeAlternativePeek(),true);
  window.addEventListener('resize', () => closeAlternativePeek());

  function optionMarkup(item, prefix) {
    return `<button class="design-option" role="tab" id="${prefix}-${item.id}" aria-selected="${item.id === selected}" ${prefix === 'design' ? 'aria-controls="slide-stage"' : 'aria-controls="compare-candidate"'} tabindex="${item.id === selected ? '0' : '-1'}" data-design="${item.id}" aria-label="${item.name}${item.id === current ? ', in slide' : ''}"><span class="design-option-image" aria-hidden="true">${item.html}</span><span class="design-option-label">${item.letter ? `<span class="option-letter" aria-hidden="true">${item.letter}</span>` : ''}<strong>${item.name}</strong></span>${item.id === current ? `<span class="in-slide-mark" aria-hidden="true">${icon('check')}In slide</span>` : ''}</button>`;
  }

  function render({focus = false} = {}) {
    const item = design(selected), inSlide = selectedIsCurrent();
    $('slide-stage').innerHTML = item.html;
    $('slide-stage').setAttribute('role','tabpanel');
    $('slide-stage').setAttribute('aria-labelledby', `design-${selected}`);
    $('preview-state').classList.toggle('is-current', inSlide);
    $('preview-state').innerHTML = inSlide ? `${icon('check')}In slide` : 'Preview';
    $('preview-name').textContent = item.name;
    $('back-current').hidden = inSlide;
    $('use-design').hidden = inSlide;
    $('approve').hidden = !inSlide;
    $('approve').setAttribute('aria-pressed',String(approved));
    $('approve').innerHTML = `${icon('check')}${approved ? 'Approved' : 'Approve'}`;
    $('design-options').innerHTML = designs.map(item => optionMarkup(item,'design')).join('');
    $('composer-design').textContent = inSlide ? '' : `${item.name} preview`;
    if ($('compare-dialog').open) renderComparison();
    if (focus) $(`design-${selected}`).focus({preventScroll:true});
  }

  function preview(id, {focus = false} = {}) {
    selected = id;
    render({focus});
    announce(`${design(id).name} ${selectedIsCurrent() ? 'is in the slide' : 'preview. Your slide is unchanged'}.`);
  }

  function renderComparison() {
    $('compare-reference').innerHTML = designs.map(item => `<option value="${item.id}" ${item.id === compareReference ? 'selected' : ''} ${item.id === selected ? 'disabled' : ''}>${item.name}${item.id === current ? ' (in slide)' : ''}</option>`).join('');
    $('compare-reference-state').innerHTML = compareReference === current ? '<span class="current-dot"></span>In slide' : 'Reference';
    $('compare-current').innerHTML = design(compareReference).html;
    $('compare-candidate-select').innerHTML = designs.filter(item => item.id !== compareReference).map(item => `<option value="${item.id}" ${item.id === selected ? 'selected' : ''}>${item.name}${item.id === current ? ' (in slide)' : ''}</option>`).join('');
    $('compare-candidate').innerHTML = design(selected).html;
    $('compare-candidate').setAttribute('role','tabpanel');
    $('compare-candidate').setAttribute('aria-labelledby',`compare-${selected}`);
    $('compare-options').innerHTML = designs.filter(item => item.id !== compareReference).map(item => optionMarkup(item,'compare')).join('');
    $('compare-candidate-state').textContent = selectedIsCurrent() ? 'In slide' : 'Preview';
    $('use-reference').hidden = compareReference === current;
    $('use-reference').textContent = `Use ${design(compareReference).name}`;
    $('use-compared').innerHTML = selectedIsCurrent() ? `${icon('check')}In slide` : `Use ${design(selected).name}${icon('right')}`;
    $('use-compared').disabled = selectedIsCurrent();
  }

  function closeComparison() {
    $('compare-dialog').close();
  }

  function adopt() {
    if (selectedIsCurrent()) return;
    previousChoice = {id:current,approved};
    current = selected;
    approved = false;
    renderRail();
    if ($('compare-dialog').open) closeComparison();
    render();
    $('toast-message').textContent = `${design(current).name} is now in the slide.`;
    $('draft-toast').hidden = false;
    $('undo-choice').hidden = false;
    $('design-toggle').focus({preventScroll:true});
  }

  function tabKeyboard(event, prefix) {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const options = [...event.currentTarget.querySelectorAll('[data-design]')];
    const from = options.findIndex(option => option.dataset.design === selected);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length-1 : (from + (event.key === 'ArrowRight' ? 1 : -1) + options.length) % options.length;
    event.preventDefault();
    preview(options[next].dataset.design);
    $(`${prefix}-${selected}`).focus({preventScroll:true});
    $(`${prefix}-${selected}`).scrollIntoView({block:'nearest',inline:'nearest'});
  }

  $('design-options').addEventListener('click', event => { const option = event.target.closest('[data-design]'); if (option) preview(option.dataset.design,{focus:true}); });
  $('design-options').addEventListener('keydown', event => tabKeyboard(event,'design'));
  $('compare-options').addEventListener('click', event => { const option = event.target.closest('[data-design]'); if (option) { preview(option.dataset.design); $(`compare-${selected}`).focus({preventScroll:true}); } });
  $('compare-options').addEventListener('keydown', event => tabKeyboard(event,'compare'));
  $('back-current').addEventListener('click', () => preview(current,{focus:true}));
  $('use-design').addEventListener('click', adopt);
  $('use-compared').addEventListener('click', adopt);
  $('use-reference').addEventListener('click', () => { selected = compareReference; adopt(); });
  $('compare-reference').addEventListener('change', event => { compareReference = event.target.value; renderComparison(); announce(`Comparing ${design(compareReference).name} with ${design(selected).name}. Your slide is unchanged.`); });
  $('compare-candidate-select').addEventListener('change', event => { preview(event.target.value); $('compare-candidate-select').focus({preventScroll:true}); });
  $('compare').addEventListener('click', event => {
    compareTrigger = event.currentTarget;
    compareReference = current;
    if (selectedIsCurrent()) selected = designs.find(item => item.id !== current).id;
    render(); renderComparison(); $('compare-dialog').showModal();
    $('close-compare').focus();
  });
  $('close-compare').addEventListener('click', closeComparison);
  $('compare-dialog').addEventListener('close', () => { compareTrigger?.focus({preventScroll:true}); });
  $('design-toggle').addEventListener('click', () => { const expanded = $('design-toggle').getAttribute('aria-expanded') === 'true'; $('design-tray').hidden = expanded; $('design-toggle').setAttribute('aria-expanded',String(!expanded)); });
  $('review-designs').addEventListener('click', () => openDesignOptions());
  $('undo-choice').addEventListener('click', () => {
    if (previousChoice === null) return;
    current = previousChoice.id; approved = previousChoice.approved; selected = current; previousChoice = null;
    renderRail(); render();
    $('toast-message').textContent = `Back to ${design(current).name.toLowerCase()}. All designs are kept.`;
    $('undo-choice').hidden = true;
    $('design-toggle').focus({preventScroll:true});
  });
  $('close-toast').addEventListener('click', () => { $('draft-toast').hidden = true; $('design-toggle').focus({preventScroll:true}); });

  function renderSlide() {
    renderRail();
    $('slide-title').textContent = slides[activeSlide].name;
    $('decision-bar').hidden = activeSlide !== 1;
    $('design-tray').hidden = activeSlide !== 1;
    $('design-toggle').hidden = activeSlide !== 1;
    doc.querySelector('.draft-chat-title h2').textContent = slides[activeSlide].name;
    doc.querySelector('.slide-number').textContent = `0${activeSlide+1}`;
    doc.querySelector('.message-count').textContent = activeSlide === 1 ? '2' : '0';
    doc.querySelector('.draft-messages').hidden = activeSlide !== 1;
    $('composer-slide').textContent = `0${activeSlide+1}`;
    $('composer-design').textContent = '';
    $('footer-slide').textContent = `${activeSlide+1} / 6`;
    if (activeSlide === 1) { render(); $('design-toggle').setAttribute('aria-expanded','true'); }
    else { $('slide-stage').innerHTML = slides[activeSlide].html; $('slide-stage').removeAttribute('aria-labelledby'); $('slide-stage').setAttribute('role','region'); $('approve').hidden = false; }
  }
  $('slide-overview').addEventListener('click', event => { if (event.target.closest('[data-alternatives]')) { openDesignOptions(); return; } const thumb = event.target.closest('[data-slide]'); if (!thumb) return; activeSlide = Number(thumb.dataset.slide); selected = current; renderSlide(); doc.body.classList.remove('slides-open'); $('toggle-slides').setAttribute('aria-expanded','false'); if (innerWidth > 600) doc.querySelector(`[data-slide="${activeSlide}"]`).focus({preventScroll:true}); else $('toggle-slides').focus(); });
  $('toggle-theme').addEventListener('click', () => { const dark = doc.documentElement.dataset.theme !== 'dark'; doc.documentElement.dataset.theme = dark ? 'dark' : 'light'; $('toggle-theme').setAttribute('aria-label',`Switch to ${dark ? 'light' : 'dark'} appearance`); });
  $('toggle-slides').addEventListener('click', () => { const open = doc.body.classList.toggle('slides-open'); $('toggle-slides').setAttribute('aria-expanded',String(open)); });
  $('toggle-chat').addEventListener('click', () => { const narrow = innerWidth <= 1100; doc.body.classList.toggle(narrow ? 'chat-open' : 'chat-closed'); $('toggle-chat').setAttribute('aria-expanded',String(narrow ? doc.body.classList.contains('chat-open') : !doc.body.classList.contains('chat-closed'))); });
  $('draft-composer').addEventListener('submit', event => { event.preventDefault(); const input = $('draft-message'); if (!input.value.trim()) return; const article = doc.createElement('article'); article.className = 'draft-message user'; const paragraph = doc.createElement('p'); paragraph.textContent = input.value; article.append(paragraph); doc.querySelector('.draft-messages').append(article); input.value = ''; article.scrollIntoView({block:'nearest'}); announce('Message added to this interaction draft.'); });
  doc.querySelectorAll('.draft-mode button').forEach(button => button.addEventListener('click', () => { doc.querySelectorAll('.draft-mode button').forEach(item => item.setAttribute('aria-pressed',String(item === button))); }));
  $('approve').addEventListener('click', () => { approved = !approved; $('approve').setAttribute('aria-pressed',String(approved)); $('approve').innerHTML = `${icon('check')}${approved ? 'Approved' : 'Approve'}`; });
  $('present').addEventListener('click', () => { $('presentation-stage').innerHTML = activeSlide === 1 ? design(current).html : slides[activeSlide].html; $('presentation-dialog').showModal(); });
  $('close-present').addEventListener('click', () => $('presentation-dialog').close());
  doc.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('compare-dialog').open && !$('presentation-dialog').open) { doc.body.classList.remove('slides-open','chat-open'); if (!selectedIsCurrent()) preview(current,{focus:true}); } });
  $('chat-design-previews').innerHTML = designs.slice(1).map(item => `<span aria-hidden="true">${item.html}</span>`).join('');
  renderRail(); render();
  if (innerWidth <= 1100) $('toggle-chat').setAttribute('aria-expanded','false');
})();
