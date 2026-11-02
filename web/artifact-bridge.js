/* Review selection inside an opaque-origin artifact, without altering normal interaction. */
(() => {
  'use strict';
  const bridge = document.currentScript;
  if (!bridge?.src) return;
  const parentOrigin = new URL(bridge.src).origin;
  const bundleRoot = bridge.dataset.artifactRoot;
  const format = bridge.dataset.artifactFormat;
  const send = message => { if (parent !== window) parent.postMessage(message, parentOrigin); };
  let selecting = false, hovered = null, selectors = [], overlay = null, marks = null;
  let scheduled = false, lastHeight = 0, inspectedSelector = null, lastGeometry = '';

  /** Resolve selectors defensively because anchors may outlive their original DOM. */
  function selectedElement(selector) {
    try { return document.querySelector(selector); } catch { return null; }
  }

  /** Prefer an authored ID, falling back to a structural path within this revision. */
  function selectorFor(element) {
    const parts = [];
    for (let node = element; node; node = node.parentElement) {
      if (node.id && node.id.length < 500) {
        const identifier = '#' + CSS.escape(node.id);
        if (document.querySelectorAll(identifier).length === 1) {
          parts.unshift(identifier);
          break;
        }
      }
      let name = CSS.escape(node.localName);
      if (node.parentElement) {
        const siblings = [...node.parentElement.children].filter(item => item.localName === node.localName);
        if (siblings.length > 1) name += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(name);
    }
    const result = parts.join(' > ');
    return result.length <= 2000 ? result : '';
  }

  let draft = null;

  /** Describe a selected element using plain data, never authored markup. */
  function inspect(selector, requestId = null) {
    const element = selectedElement(selector);
    if (!element || !(element instanceof HTMLElement || element instanceof SVGElement) || element === overlay || overlay?.contains(element)) {
      if (requestId !== null) send({type: 'artifact:edit-error', request_id: requestId, message: 'Element is no longer available.'});
      return;
    }
    const computed = getComputedStyle(element);
    const authoredSize = element.style.getPropertyValue('font-size');
    const size = /^(\d+(?:\.\d+)?)(px|pt|em|rem|%|vw|vh|cqw|cqh)$/.exec(authoredSize.trim()) || /^(\d+(?:\.\d+)?)(px)$/.exec(computed.fontSize);
    const rect = element.getBoundingClientRect();
    inspectedSelector = selector;
    const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(computed.color);
    const color = rgb ? '#' + rgb.slice(1).map(value => Number(value).toString(16).padStart(2, '0')).join('') : computed.color;
    send({type: 'artifact:inspector', request_id: requestId, selector, label: (element.getAttribute('aria-label') || element.localName).slice(0, 500),
      text: element.childElementCount === 0 ? (element.textContent || '').slice(0, 9000) : '',
      canText: element.childElementCount === 0 && (element.textContent || '').length <= 9000 && !['script','style','input','textarea','img','br','hr'].includes(element.localName),
      typography: {font_size: size ? Number(size[1]) : 16, font_size_unit: size ? size[2] : 'px', font_size_px: Number.parseFloat(computed.fontSize) || 16, font_weight: computed.fontWeight,
        font_style: computed.fontStyle === 'italic' ? 'italic' : 'normal', text_align: computed.textAlign,
        color},
      rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height},
      dirty: !!draft && draft.selector === selector && Object.keys(draft.changes).length > 0,
      changes: draft?.selector === selector ? {...draft.changes} : {}});
  }

  /** Restore only properties touched by the preview, including their authored priorities. */
  function cancelEdit() {
    if (!draft) return;
    const {element, original, text} = draft;
    for (const [name, saved] of Object.entries(original)) {
      if (saved.value) element.style.setProperty(name, saved.value, saved.priority);
      else element.style.removeProperty(name);
    }
    if (text !== null) element.textContent = text;
    const selector = draft.selector;
    draft = null;
    inspect(selector);
    schedule();
  }

  /** Validate a bounded patch before mutating the sandboxed preview. */
  function previewEdit(selector, values, requestId = null) {
    const element = selectedElement(selector);
    if (!element || !(element instanceof HTMLElement || element instanceof SVGElement)) throw Error('Element is no longer available.');
    if (draft && draft.selector !== selector) throw Error('Send or cancel the edit first.');
    if (!values || typeof values !== 'object' || Array.isArray(values)) throw Error('Invalid edit.');
    const keys = Object.keys(values);
    if (!keys.length || keys.some(key => !['text', 'font_size', 'font_size_unit', 'font_weight', 'font_style', 'color', 'text_align'].includes(key))) throw Error('Invalid edit property.');
    const styles = {};
    if ('text' in values && (typeof values.text !== 'string' || values.text.length > 9000 || (element.textContent || '').length > 9000 || element.childElementCount || ['script','style','input','textarea','img','br','hr'].includes(element.localName))) throw Error('This element cannot be edited as plain text.');
    if ('font_size' in values || 'font_size_unit' in values) {
      if (typeof values.font_size !== 'number' || !Number.isFinite(values.font_size) || values.font_size <= 0 || values.font_size > 10000 || !['px','pt','em','rem','%','vw','vh','cqw','cqh'].includes(values.font_size_unit)) throw Error('Invalid font size.');
      styles['font-size'] = `${values.font_size}${values.font_size_unit}`;
    }
    if ('font_weight' in values) {
      if (typeof values.font_weight !== 'string' || !/^(normal|bold|bolder|lighter|[1-9]00)$/.test(values.font_weight)) throw Error('Invalid font weight.');
      styles['font-weight'] = values.font_weight;
    }
    if ('font_style' in values) {
      if (!['normal','italic'].includes(values.font_style)) throw Error('Invalid font style.');
      styles['font-style'] = values.font_style;
    }
    if ('text_align' in values) {
      if (!['left','center','right','justify'].includes(values.text_align)) throw Error('Invalid alignment.');
      styles['text-align'] = values.text_align;
    }
    if ('color' in values) {
      if (typeof values.color !== 'string' || values.color.length > 100 || !CSS.supports('color', values.color) || /[;{}]/.test(values.color)) throw Error('Invalid color.');
      styles.color = values.color;
    }
    if (!draft) draft = {selector, element, original: {}, text: null, changes: {}};
    for (const [name, value] of Object.entries(styles)) {
      if (!draft.original[name]) draft.original[name] = {value: element.style.getPropertyValue(name), priority: element.style.getPropertyPriority(name)};
      element.style.setProperty(name, value, 'important');
    }
    if ('text' in values) {
      if (draft.text === null) draft.text = element.textContent || '';
      element.textContent = values.text;
    }
    Object.assign(draft.changes, values);
    inspect(selector, requestId);
    schedule();
  }

  /** Redraw outlines without adding styles or attributes to authored elements. */
  function draw() {
    scheduled = false;
    if (!document.body) return;
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.setAttribute('data-artifact-bridge', '');
      overlay.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;contain:strict;';
      const shadow = overlay.attachShadow({mode: 'closed'});
      const style = document.createElement('style');
      style.textContent = ':host{pointer-events:none!important}.mark{position:absolute;box-sizing:border-box;border:2px solid #7164df;border-radius:3px;pointer-events:none}.hover{border-style:dashed;border-color:#367ddb}';
      marks = document.createElement('div');
      shadow.append(style, marks);
      document.documentElement.append(overlay);
    }
    marks.replaceChildren();
    const items = selectors.map(selectedElement).filter(Boolean).map(element => ({element, hover: false}));
    if (selecting && hovered && !items.some(item => item.element === hovered)) items.push({element: hovered, hover: true});
    for (const {element, hover} of items) {
      if (!element.isConnected) continue;
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      const mark = document.createElement('div');
      mark.className = hover ? 'mark hover' : 'mark';
      mark.style.left = `${bounds.left}px`;
      mark.style.top = `${bounds.top}px`;
      mark.style.width = `${bounds.width}px`;
      mark.style.height = `${bounds.height}px`;
      marks.append(mark);
    }
    const height = Math.ceil(Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
    if(selecting && inspectedSelector && selectors.length===1 && selectors[0]===inspectedSelector){const rect=selectedElement(inspectedSelector)?.getBoundingClientRect();if(rect){const geometry={type:'artifact:geometry',selector:inspectedSelector,rect:{left:rect.left,top:rect.top,width:rect.width,height:rect.height}},key=JSON.stringify(geometry);if(key!==lastGeometry){lastGeometry=key;send(geometry);}}}
    if (Number.isFinite(height) && height > 0 && height !== lastHeight) {
      lastHeight = height;
      send({type: 'artifact:height', height});
    }
  }

  function schedule() {
    if (!scheduled) { scheduled = true; requestAnimationFrame(draw); }
  }

  /** Send plain anchor data; no content from the artifact becomes workspace markup. */
  function choose(event) {
    const element = event.target instanceof Element ? event.target : null;
    if (!element || element === overlay || overlay?.contains(element)) return;
    const selector = selectorFor(element);
    if (!selector) return;
    if (draft && draft.selector !== selector) { send({type: 'artifact:edit-error', message: 'Send or cancel the edit first.'}); return; }
    const text = (element instanceof HTMLElement ? element.innerText : element.textContent) || '';
    const quote = text.replace(/\s+/g, ' ').trim().slice(0, 1000);
    const label = element.getAttribute('aria-label') || element.getAttribute('alt') || element.getAttribute('title') || quote || element.localName;
    send({
      type: 'artifact:target',
      target: {kind: 'element', selector, quote, label: label.slice(0, 500), viewport: {width: innerWidth, height: innerHeight}},
      additive: !!(event.metaKey || event.ctrlKey || event.shiftKey)
    });
  }

  for (const name of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'auxclick', 'contextmenu', 'touchstart', 'touchend']) {
    window.addEventListener(name, event => {
      if (!selecting) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (name === 'click') choose(event);
    }, {capture: true, passive: false});
  }
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape') send({type: 'artifact:escape'});
    if (!selecting || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    choose(event);
  }, true);
  window.addEventListener('pointermove', event => {
    if (!selecting) return;
    hovered = event.target instanceof Element ? event.target : null;
    schedule();
  }, true);
  window.addEventListener('pointerout', event => {
    if (selecting && !event.relatedTarget) { hovered = null; schedule(); }
  }, true);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== parentOrigin) return;
    const message = event.data;
    if (!message || typeof message !== 'object') return;
    if (message.type === 'artifact:inspect' && typeof message.selector === 'string' && message.selector.length <= 2000) {
      if (draft && draft.selector !== message.selector) send({type: 'artifact:edit-error', message: 'Send or cancel the edit first.'});
      else inspect(message.selector, message.request_id);
    } else if (message.type === 'artifact:edit-preview' && typeof message.selector === 'string' && message.selector.length <= 2000) {
      try { previewEdit(message.selector, message.values, message.request_id); } catch (error) { send({type: 'artifact:edit-error', request_id: message.request_id, message: error.message}); }
    } else if (message.type === 'artifact:edit-cancel') { cancelEdit();
    } else if (message.type === 'artifact:edit-accept') { const selector = draft?.selector; draft = null; if (selector) inspect(selector);
    } else if (message.type === 'artifact:select' && typeof message.enabled === 'boolean') {
      selecting = message.enabled;
      if (!selecting) hovered = null;
      schedule();
    } else if (message.type === 'artifact:selection' && Array.isArray(message.selectors)) {
      selectors = message.selectors.filter(value => typeof value === 'string' && value.length <= 2000).slice(0, 100);
      schedule();
    }
  });
  window.addEventListener('resize', schedule);
  window.addEventListener('scroll', schedule, true);
  window.addEventListener('load', schedule);

  /** Render Markdown with escaped raw HTML and bundle-scoped resource URLs. */
  function renderMarkdown() {
    const source = document.getElementById('artifact-markdown-source');
    const target = document.getElementById('artifact-markdown');
    if (format !== 'markdown' || !source || !target || !window.marked) return;
    const escape = value => String(value).replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
    const localURL = value => {
      try {
        const url = new URL(value, document.baseURI);
        return url.href.startsWith(bundleRoot) ? url.href : '';
      } catch { return ''; }
    };
    const renderer = new window.marked.Renderer();
    renderer.html = token => escape(token.text);
    renderer.link = function(token) {
      const label = this.parser.parseInline(token.tokens), href = localURL(token.href);
      return href ? `<a href="${escape(href)}">${label}</a>` : label;
    };
    renderer.image = token => {
      const src = localURL(token.href), label = escape(token.text || '');
      return src ? `<img src="${escape(src)}" alt="${label}">` : label;
    };
    target.innerHTML = new window.marked.Marked({renderer, gfm: true}).parse(source.textContent, {async: false});
    source.remove();
  }

  function start() {
    renderMarkdown();
    const resize = new ResizeObserver(schedule);
    resize.observe(document.documentElement);
    if (document.body) resize.observe(document.body);
    const mutations = new MutationObserver(records => {
      if (records.some(record => record.target !== overlay && !overlay?.contains(record.target))) schedule();
    });
    mutations.observe(document.documentElement, {childList: true, subtree: true, characterData: true, attributes: true});
    document.fonts?.ready.then(schedule);
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
