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
    let compactChatOpen = false, compactSidebarOpen = false;
    const covered = new Map();
    const compact = () => kind === 'slides' && view.innerWidth <= 1050;
    const narrow = () => kind === 'slides' && view.innerWidth <= 600;
    const closed = side => side === 'right' && compact() ? !compactChatOpen : side === 'left' && narrow() ? !compactSidebarOpen : state[side];
    const persist = () => { try { storage?.setItem(key, JSON.stringify({left:state.left,right:state.right})); } catch {} };
    const resizeBounds = side => ({min:120,max:kind === 'slides' && side === 'left' ? Math.max(120,Math.min(650,Math.floor(view.innerWidth * .26))) : 650});
    const uncover = () => { for (const [element,inert] of covered) element.inert = inert; covered.clear(); };
    // Keep covered controls out of keyboard navigation without replacing the authored iframe.
    const updateDrawer = sorting => {
      const open = kind === 'slides' && !state.focus && !sorting && (compact() && compactChatOpen || narrow() && compactSidebarOpen);
      const canvas = open ? workspace?.querySelector('.canvas-area') : null;
      for (const [element,inert] of covered) if (element !== canvas) { element.inert = inert; covered.delete(element); }
      if (canvas && !covered.has(canvas)) { covered.set(canvas,canvas.inert); canvas.inert = true; }
    };
    const positionHandles = () => {
      if (!workspace) return;
      for (const [side, panel] of [['left',left],['right',right]]) {
        if (!panel) continue;
        const rect = panel.getBoundingClientRect(), handle = handles[side];
        const bounds = resizeBounds(side), width = Math.round(rect.width);
        handle.setAttribute('aria-valuemin', String(closed(side) ? 0 : bounds.min));
        handle.setAttribute('aria-valuemax', String(Math.max(bounds.max,width)));
        handle.setAttribute('aria-valuenow', String(width));
        handle.setAttribute('aria-valuetext', `${side === 'left' ? 'Sidebar' : 'Chat'} width ${width} pixels`);
        handle.style.left = `${(side === 'left' ? rect.right : rect.left) - 4}px`;
        if (kind === 'hub' && side === 'left') controls.left.style.left = `${state.left ? 8 : Math.max(8, rect.right - 32)}px`;
        handle.style.top = `${Math.max(0,rect.top)}px`;
        handle.style.height = `${Math.max(0,Math.min(view.innerHeight,rect.bottom)-Math.max(0,rect.top))}px`;
      }
    };
    const apply = () => {
      if (!workspace) return;
      const active = doc.activeElement;
      const sorting = kind === 'slides' && workspace.classList.contains('is-sorter');
      const hiddenFocus = closed('right') && (right?.contains?.(active) || active === handles.right) ? 'right' : closed('left') && (left?.contains?.(active) || active === handles.left) && !sorting ? 'left' : null;
      workspace.classList.toggle('panels-left-closed', closed('left'));
      if (kind === 'slides') { if (closed('left')) workspace.style.setProperty('--chat-rail-width','0px'); else if (workspace.style.getPropertyValue('--panel-left-width')) workspace.style.setProperty('--chat-rail-width',workspace.style.getPropertyValue('--panel-left-width')); else workspace.style.removeProperty('--chat-rail-width'); }
      workspace.classList.toggle('panels-right-closed', closed('right'));
      doc.body.classList.toggle('workspace-clean-view', state.focus);
      if (lastFocus !== state.focus) { if(state.focus)focusTrigger=doc.activeElement;else focusTrigger?.focus?.({preventScroll:true});lastFocus = state.focus; onViewChange(state.focus); }
      for (const side of ['left','right']) {
        const button = controls[side];
        const available = side === 'left' ? !!left : !!right;
        button.hidden = state.focus || !available || sorting;
        const label = `${closed(side) ? 'Open' : 'Close'} ${side === 'left' ? 'sidebar' : 'chat'}`;
        button.setAttribute('aria-label', label); button.title = label;
        button.setAttribute('aria-expanded', String(!closed(side)));
        if (kind === 'slides') button.setAttribute('aria-controls',side === 'left' ? 'slides' : 'chat-panel');
        const direction = side === 'left' ? (state.left ? 'right' : 'left') : (state.right ? 'left' : 'right');
        if (view.SlideIcons?.svg) button.innerHTML = view.SlideIcons.svg(kind === 'slides' ? (side === 'left' ? 'panelLeft' : 'chat') : direction);
        else button.textContent = direction === 'left' ? '‹' : '›';
        handles[side].hidden = state.focus || !available || sorting || closed(side) || (kind === 'slides' && (side === 'right' || narrow()));
      }
      exit.hidden = !state.focus; exitArea.hidden = !state.focus;
      updateDrawer(sorting);
      positionHandles();
      if (hiddenFocus && !controls[hiddenFocus].hidden && !doc.querySelector('dialog[open]')) controls[hiddenFocus].focus?.({preventScroll:true});
    };
    const toggle = side => {
      if (side === 'right' && compact()) { compactChatOpen = !compactChatOpen; if (compactChatOpen && narrow()) compactSidebarOpen = false; }
      else if (side === 'left' && narrow()) { compactSidebarOpen = !compactSidebarOpen; if (compactSidebarOpen) compactChatOpen = false; }
      else { state[side] = !state[side]; persist(); }
      apply();
      if (kind === 'slides' && !closed(side)) {
        const target = side === 'right' ? right?.querySelector?.('textarea') : left?.querySelector?.('.slide-thumb[aria-current=true]');
        target?.focus?.({preventScroll:true});
      }
    };
    for (const side of ['left','right']) {
      const button = doc.createElement('button'); button.type = 'button'; button.hidden = true; button.className = `workspace-panel-toggle panel-${side}`;
      button.addEventListener('click', () => toggle(side));
      const topbar = kind === 'slides' ? doc.querySelector('.topbar') : null;
      if (topbar?.insertBefore) {
        if (side === 'left') topbar.insertBefore(button,doc.querySelector('.identity'));
        else doc.querySelector('.top-actions').append(button);
      }
      else doc.body.append(button);
      controls[side] = button;
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
        const width = drag.width + (side === 'left' ? event.clientX - drag.start : drag.start - event.clientX), bounds = resizeBounds(side);
        workspace.style.setProperty(`--panel-${side}-width`, `${Math.max(bounds.min, Math.min(bounds.max, width))}px`);
        if (kind === 'slides' && side === 'left') workspace.style.setProperty('--chat-rail-width', `${Math.max(bounds.min, Math.min(bounds.max, width))}px`);
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
        if (!['ArrowLeft','ArrowRight','Home','End','Enter'].includes(event.key)) return;
        event.preventDefault();
        if (event.key === 'Enter') { toggle(side); return; }
        const panel = side === 'left' ? left : right, delta = event.key === 'ArrowRight' ? 20 : -20, bounds = resizeBounds(side);
        const width = event.key === 'Home' ? bounds.min : event.key === 'End' ? bounds.max : panel.getBoundingClientRect().width + (side === 'left' ? delta : -delta);
        if (width < 120) { state[side] = true; persist(); apply(); }
        else { workspace.style.setProperty(`--panel-${side}-width`,`${Math.min(bounds.max,width)}px`); if (kind === 'slides' && side === 'left') workspace.style.setProperty('--chat-rail-width',`${Math.min(bounds.max,width)}px`); positionHandles(); }
      });
      doc.body.append(handle); handles[side] = handle;
    }
    const exitArea = doc.createElement('div'); exitArea.className = 'workspace-clean-exit-area'; exitArea.hidden = true;
    const exit = doc.createElement('button'); exit.type = 'button'; exit.className = 'workspace-clean-exit'; exit.textContent = '×'; exit.title = 'Exit view (Esc)'; exit.setAttribute('aria-label','Exit view'); exit.hidden = true;
    exit.addEventListener('click', () => { state.focus = false; apply(); }); exitArea.append(exit); doc.body.append(exitArea);
    doc.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (state.focus) { event.preventDefault(); state.focus = false; apply(); }
      else if (compact() && compactChatOpen && !doc.querySelector('dialog[open]')) { event.preventDefault(); compactChatOpen = false; apply(); controls.right.focus?.(); }
      else if (narrow() && compactSidebarOpen && !doc.querySelector('dialog[open]')) { event.preventDefault(); compactSidebarOpen = false; apply(); controls.left.focus?.(); }
    });
    view.addEventListener('resize', apply);
    view.addEventListener('scroll', positionHandles, true);
    doc.addEventListener('workspace-collapse-chat', () => { compactChatOpen = false; state.right = true; persist(); apply(); });
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
      if (!available) { uncover(); state.focus = false; doc.body.classList.remove('workspace-clean-view'); if (lastFocus) { lastFocus = false; onViewChange(false); } exit.hidden = true; exitArea.hidden = true; return; }
      apply();
    }
    return {refresh, enterView() { if (workspace && right && !workspace.classList.contains('is-empty') && !workspace.classList.contains('is-sorter')) { state.focus = true; apply(); } }, exitView() { state.focus = false; apply(); }};
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {create};
  else root.WorkspacePanels = {create};
})(typeof window === 'undefined' ? globalThis : window);
