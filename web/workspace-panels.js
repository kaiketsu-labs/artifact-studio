/* Keep the workspace panels and artifact viewer alive while changing their layout. */
((root) => {
  'use strict';
  const key = 'artifact-studio:workspace-panels:v1';
  /** Install persistent panel controls for a hub or slide workspace. */
  function create({doc, view, storage, kind = 'hub', onViewChange = () => {}}) {
    let saved = {};
    try { saved = JSON.parse(storage?.getItem(key) || '{}') || {}; } catch {}
    const state = {left: !!saved.left, right: !!saved.right, focus: false};
    const controls = {}, handles = {};
    let workspace = null, left = null, right = null, drag = null, lastFocus = false, focusTrigger = null;
    const persist = () => { try { storage?.setItem(key, JSON.stringify({left:state.left,right:state.right})); } catch {} };
    const positionHandles = () => {
      if (!workspace) return;
      for (const [side, panel] of [['left',left],['right',right]]) {
        if (!panel) continue;
        const rect = panel.getBoundingClientRect(), handle = handles[side];
        handle.style.left = `${(side === 'left' ? rect.right : rect.left) - 4}px`;
        if (kind === 'hub' && side === 'left') controls.left.style.left = `${state.left ? 8 : Math.max(8, rect.right - 32)}px`;
        handle.style.top = `${Math.max(0,rect.top)}px`;
        handle.style.height = `${Math.max(0,Math.min(view.innerHeight,rect.bottom)-Math.max(0,rect.top))}px`;
      }
    };
    const apply = () => {
      if (!workspace) return;
      workspace.classList.toggle('panels-left-closed', state.left);
      if (kind === 'slides') { if (state.left) workspace.style.setProperty('--chat-rail-width','0px'); else if (workspace.style.getPropertyValue('--panel-left-width')) workspace.style.setProperty('--chat-rail-width',workspace.style.getPropertyValue('--panel-left-width')); else workspace.style.removeProperty('--chat-rail-width'); }
      workspace.classList.toggle('panels-right-closed', state.right);
      doc.body.classList.toggle('workspace-clean-view', state.focus);
      if (lastFocus !== state.focus) { if(state.focus)focusTrigger=doc.activeElement;else focusTrigger?.focus?.({preventScroll:true});lastFocus = state.focus; onViewChange(state.focus); }
      for (const side of ['left','right']) {
        const button = controls[side];
        const available = side === 'left' ? !!left : !!right;
        const sorting = kind === 'slides' && workspace.classList.contains('is-sorter');
        button.hidden = state.focus || !available || sorting;
        const label = `${state[side] ? 'Open' : 'Close'} ${side === 'left' ? 'sidebar' : 'chat'}`;
        button.setAttribute('aria-label', label); button.title = label;
        button.setAttribute('aria-expanded', String(!state[side]));
        const direction = side === 'left' ? (state.left ? 'right' : 'left') : (state.right ? 'left' : 'right');
        if (view.SlideIcons?.svg) button.innerHTML = view.SlideIcons.svg(direction);
        else button.textContent = direction === 'left' ? '‹' : '›';
        handles[side].hidden = state.focus || !available || sorting || state[side] || (kind === 'slides' && side === 'right');
      }
      exit.hidden = !state.focus; exitArea.hidden = !state.focus;
      positionHandles();
    };
    const toggle = side => { state[side] = !state[side]; persist(); apply(); };
    for (const side of ['left','right']) {
      const button = doc.createElement('button'); button.type = 'button'; button.hidden = true; button.className = `workspace-panel-toggle panel-${side}`;
      button.addEventListener('click', () => toggle(side)); doc.body.append(button); controls[side] = button;
      const handle = doc.createElement('div'); handle.hidden = true; handle.className = `workspace-panel-resize panel-${side}`;
      handle.setAttribute('role','separator'); handle.setAttribute('aria-orientation','vertical'); handle.setAttribute('aria-label',`Resize ${side === 'left' ? 'sidebar' : 'chat'}`); handle.tabIndex = 0;
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !workspace) return;
        event.preventDefault();
        const panel = side === 'left' ? left : right;
        drag = {side, id:event.pointerId, start:event.clientX, width:panel.getBoundingClientRect().width, previous:workspace.style.getPropertyValue(`--panel-${side}-width`), rail:workspace.style.getPropertyValue('--chat-rail-width')};
        handle.setPointerCapture(event.pointerId); doc.body.classList.add('workspace-panel-dragging');
      });
      handle.addEventListener('pointermove', event => {
        if (!drag || drag.id !== event.pointerId) return;
        const width = drag.width + (side === 'left' ? event.clientX - drag.start : drag.start - event.clientX);
        workspace.style.setProperty(`--panel-${side}-width`, `${Math.max(120, Math.min(650, width))}px`);
        if (kind === 'slides' && side === 'left') workspace.style.setProperty('--chat-rail-width', `${Math.max(120, Math.min(650, width))}px`);
        positionHandles();
        handle.classList.toggle('is-closing',width < 100);
      });
      const finish = (event, commit) => {
        if (!drag || drag.id !== event.pointerId) return;
        const width = drag.width + (side === 'left' ? event.clientX - drag.start : drag.start - event.clientX);
        const previous = drag; drag = null;
        if (!commit || width < 100) {
          const restore = (name,value) => value ? workspace.style.setProperty(name,value) : workspace.style.removeProperty(name);
          restore(`--panel-${side}-width`,previous.previous);
          if (kind === 'slides' && side === 'left') restore('--chat-rail-width',previous.rail);
        }
        if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
        doc.body.classList.remove('workspace-panel-dragging'); handle.classList.remove('is-closing');
        if (commit && width < 100) { state[side] = true; persist(); }
        apply();
      };
      handle.addEventListener('pointerup', event => finish(event,true));
      handle.addEventListener('pointercancel', event => finish(event,false));
      handle.addEventListener('keydown', event => {
        if (!['ArrowLeft','ArrowRight','Enter'].includes(event.key)) return;
        event.preventDefault();
        if (event.key === 'Enter') { toggle(side); return; }
        const panel = side === 'left' ? left : right, delta = event.key === 'ArrowRight' ? 20 : -20;
        const width = panel.getBoundingClientRect().width + (side === 'left' ? delta : -delta);
        if (width < 120) { state[side] = true; persist(); apply(); }
        else { workspace.style.setProperty(`--panel-${side}-width`,`${Math.min(650,width)}px`); if (kind === 'slides' && side === 'left') workspace.style.setProperty('--chat-rail-width',`${Math.min(650,width)}px`); positionHandles(); }
      });
      doc.body.append(handle); handles[side] = handle;
    }
    const exitArea = doc.createElement('div'); exitArea.className = 'workspace-clean-exit-area'; exitArea.hidden = true;
    const exit = doc.createElement('button'); exit.type = 'button'; exit.className = 'workspace-clean-exit'; exit.textContent = '×'; exit.title = 'Exit view (Esc)'; exit.setAttribute('aria-label','Exit view'); exit.hidden = true;
    exit.addEventListener('click', () => { state.focus = false; apply(); }); exitArea.append(exit); doc.body.append(exitArea);
    doc.addEventListener('keydown', event => { if (event.key === 'Escape' && state.focus) { event.preventDefault(); state.focus = false; apply(); } });
    view.addEventListener('resize', positionHandles);
    view.addEventListener('scroll', positionHandles, true);
    doc.addEventListener('workspace-collapse-chat', () => { state.right = true; persist(); apply(); });
    /** Rebind to newly rendered hub elements without touching their contents. */
    function refresh() {
      workspace = doc.querySelector(kind === 'hub' ? '.hub-layout' : '.workspace');
      left = workspace?.querySelector(kind === 'hub' ? '.hub-sidebar' : '.rail');
      right = workspace?.querySelector(kind === 'hub' ? '.hub-chat' : '.discussion');
      const available = !!(workspace && left);
      if (kind === 'slides' && workspace && view.MutationObserver && !workspace._workspacePanelsObserved) {
        new view.MutationObserver(apply).observe(workspace,{attributes:true,attributeFilter:['class']});
        workspace._workspacePanelsObserved = true;
      }
      for (const element of [...Object.values(controls),...Object.values(handles)]) element.hidden = !available;
      if (!available) { state.focus = false; doc.body.classList.remove('workspace-clean-view'); if (lastFocus) { lastFocus = false; onViewChange(false); } exit.hidden = true; exitArea.hidden = true; return; }
      apply();
    }
    return {refresh, enterView() { if (workspace && right && !workspace.classList.contains('is-empty') && !workspace.classList.contains('is-sorter')) { state.focus = true; apply(); } }, exitView() { state.focus = false; apply(); }};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.WorkspacePanels = {create};
})(typeof window === 'undefined' ? globalThis : window);
