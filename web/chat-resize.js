/* A local width preference for the desktop conversation pane. */
((root) => {
  'use strict';
  const key = 'artifact-studio:chat-width:v1';
  const minimum = 280, maximum = 800;

  function create({workspace, handle, view, storage}) {
    let preferred = null, drag = null;
    try {
      const saved = Number(storage.getItem(key));
      if (Number.isFinite(saved) && saved >= minimum && saved <= maximum) preferred = saved;
    } catch {}

    function limits() {
      const style = view.getComputedStyle(workspace);
      const number = name => parseFloat(style.getPropertyValue(name));
      return {
        min: minimum,
        max: Math.max(minimum, Math.min(maximum, Math.floor(workspace.clientWidth
          - number('--chat-rail-width') - number('--chat-canvas-min') - number('--chat-divider-width')))),
        default: number('--chat-default-width')
      };
    }
    const available = () => view.matchMedia('(min-width: 901px)').matches
      && !workspace.classList.contains('is-empty') && !workspace.classList.contains('is-sorter');
    function apply() {
      const bounds = limits();
      const width = Math.round(Math.max(bounds.min, Math.min(bounds.max, preferred ?? bounds.default)));
      workspace.style.setProperty('--chat-width', `${width}px`);
      handle.setAttribute('aria-valuemin', String(bounds.min));
      handle.setAttribute('aria-valuemax', String(bounds.max));
      handle.setAttribute('aria-valuenow', String(width));
      handle.setAttribute('aria-valuetext', `Chat width ${width} pixels`);
      return width;
    }
    function save() {
      try {
        if (preferred === null) storage.removeItem(key);
        else storage.setItem(key, String(Math.round(preferred)));
      } catch {}
    }
    function finish(commit) {
      if (!drag) return;
      const previous = drag;
      drag = null;
      if (!commit) preferred = previous.preferred;
      workspace.classList.remove('is-resizing-chat');
      if (handle.hasPointerCapture(previous.id)) handle.releasePointerCapture(previous.id);
      apply();
      if (commit) save();
    }
    handle.addEventListener('pointerdown', event => {
      if (!available() || event.button !== 0 || drag) return;
      event.preventDefault();
      handle.focus();
      drag = {id: event.pointerId, x: event.clientX, width: apply(), preferred};
      handle.setPointerCapture(event.pointerId);
      workspace.classList.add('is-resizing-chat');
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      const bounds = limits();
      preferred = Math.max(bounds.min, Math.min(bounds.max, drag.width + drag.x - event.clientX));
      apply();
    });
    handle.addEventListener('pointerup', event => {
      if (drag?.id === event.pointerId) finish(true);
    });
    handle.addEventListener('pointercancel', event => {
      if (drag?.id === event.pointerId) finish(false);
    });
    handle.addEventListener('lostpointercapture', event => {
      if (drag?.id === event.pointerId) finish(false);
    });
    handle.addEventListener('dblclick', () => {
      if (!available()) return;
      preferred = null; apply(); save();
    });
    handle.addEventListener('keydown', event => {
      if (event.key === 'Escape' && drag) { event.preventDefault(); finish(false); return; }
      if (!available() || !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter'].includes(event.key)) return;
      event.preventDefault();
      const bounds = limits(), width = apply(), step = event.shiftKey ? 50 : 20;
      preferred = event.key === 'Enter' ? null : event.key === 'Home' ? bounds.min
        : event.key === 'End' ? bounds.max : Math.max(bounds.min, Math.min(bounds.max,
          width + (event.key === 'ArrowLeft' ? step : -step)));
      apply(); save();
    });
    view.addEventListener('resize', () => { finish(false); apply(); });
    view.addEventListener('blur', () => finish(false));
    new view.MutationObserver(() => { if (!available()) finish(false); }).observe(workspace,
      {attributes: true, attributeFilter: ['class']});
    apply();
    return {reset() { preferred = null; apply(); save(); }};
  }

  if (typeof module !== 'undefined') module.exports = {create};
  else {
    let storage;
    try { storage = root.localStorage; } catch { storage = null; }
    create({workspace: root.document.querySelector('.workspace'),
      handle: root.document.getElementById('chat-resizer'), view: root, storage});
  }
})(typeof window === 'undefined' ? {} : window);
