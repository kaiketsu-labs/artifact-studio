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
      image: element.localName === 'img',
      text: element.childElementCount === 0 ? (element.textContent || '').slice(0, 9000) : '',
      canText: element.childElementCount === 0 && (element.textContent || '').length <= 9000 && !['script','style','input','textarea','img','br','hr'].includes(element.localName),
      typography: {font_size: size ? Number(size[1]) : 16, font_size_unit: size ? size[2] : 'px', font_size_px: Number.parseFloat(computed.fontSize) || 16, font_weight: computed.fontWeight,
        font_style: computed.fontStyle === 'italic' ? 'italic' : 'normal', text_align: computed.textAlign,
        color},
      rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height},
      position: {x:(rect.left+scrollX)/(draft?.canvas?.width||document.documentElement.clientWidth),y:(rect.top+scrollY)/(draft?.canvas?.height||Math.max(document.documentElement.scrollHeight,innerHeight)),width:rect.width/(draft?.canvas?.width||document.documentElement.clientWidth),height:rect.height/(draft?.canvas?.height||Math.max(document.documentElement.scrollHeight,innerHeight)),canvas_width:draft?.canvas?.width||document.documentElement.clientWidth,canvas_height:draft?.canvas?.height||Math.max(document.documentElement.scrollHeight,innerHeight)},
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
    if (!keys.length || keys.some(key => !['text', 'font_size', 'font_size_unit', 'font_weight', 'font_style', 'color', 'text_align', 'x', 'y', 'position_reference', 'canvas_reference', 'canvas_width', 'canvas_height', 'deleted'].includes(key))) throw Error('Invalid edit property.');
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
    for(const key of ['x','y'])if(key in values && (typeof values[key]!=='number'||!Number.isFinite(values[key])||values[key]<0||values[key]>1))throw Error('Invalid position.');
    if('position_reference' in values&&values.position_reference!=='visual_top_left'||'canvas_reference' in values&&values.canvas_reference!=='artifact_document')throw Error('Invalid coordinate reference.');
    if(('deleted' in values||'x' in values||'y' in values)&&(element===document.body||element===document.documentElement))throw Error('Select a specific element to move or delete.');
    if('deleted' in values){if(!['true','false'].includes(values.deleted))throw Error('Invalid deletion.');styles.visibility=values.deleted==='true'?'hidden':getComputedStyle(element).visibility;}
    let placement=draft?.placement,canvas=draft?.canvas;
    if('x' in values||'y' in values){
      if(!placement){
        const computed=getComputedStyle(element),transform=computed.transform;
        for(let node=element;node;node=node.parentElement){const style=getComputedStyle(node);if(!new DOMMatrixReadOnly(style.transform).is2D||style.perspective!=='none')throw Error('Use the conversation to position a 3D element.');}
        const saved=['transform','transition'].map(name=>[name,element.style.getPropertyValue(name),element.style.getPropertyPriority(name)]);
        try{
          element.style.setProperty('transition','none','important');
          const probe=(x,y)=>{element.style.setProperty('transform',`translate(${x}px,${y}px) ${transform==='none'?'':transform}`,'important');return element.getBoundingClientRect();};
          const rect=probe(0,0),x=probe(10,0),y=probe(0,10);canvas={width:document.documentElement.clientWidth,height:Math.max(document.documentElement.scrollHeight,innerHeight)};
          placement={left:rect.left+scrollX,top:rect.top+scrollY,transform,xx:(x.left-rect.left)/10,yx:(x.top-rect.top)/10,xy:(y.left-rect.left)/10,yy:(y.top-rect.top)/10};
        }finally{for(const [name,value,priority]of saved){if(value)element.style.setProperty(name,value,priority);else element.style.removeProperty(name);}}
      }
      for(const key of ['width','height'])if(('canvas_'+key) in values && (!Number.isFinite(values['canvas_'+key])||Math.abs(values['canvas_'+key]-canvas[key])>.001))throw Error('The preview size changed. Select the element again before moving it.');
      const determinant=placement.xx*placement.yy-placement.xy*placement.yx;
      if(!Number.isFinite(determinant)||Math.abs(determinant)<1e-8)throw Error('This element cannot be positioned in the preview.');
      const dx='x' in values?values.x*canvas.width-placement.left:0,dy='y' in values?values.y*canvas.height-placement.top:0;
      styles.transform=`translate(${(dx*placement.yy-dy*placement.xy)/determinant}px,${(dy*placement.xx-dx*placement.yx)/determinant}px) ${placement.transform==='none'?'':placement.transform}`;
      styles.transition='none';
    }
    if (!draft) draft = {selector, element, original: {}, text: null, changes: {}};
    if(placement){draft.placement=placement;draft.canvas=canvas;}
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

  function targetFor(element){
    const text=(element instanceof HTMLElement?element.innerText:element.textContent)||'',quote=text.replace(/\s+/g,' ').trim().slice(0,1000);
    const label=element.getAttribute('aria-label')||element.getAttribute('alt')||element.getAttribute('title')||quote||element.localName;
    return {kind:'element',selector:selectorFor(element),quote,label:label.slice(0,500),image:element.localName==='img',viewport:{width:innerWidth,height:innerHeight}};
  }
  const marquee=window.MarqueeSelection.create({doc:document,enabled:()=>selecting&&!draft,
    onActivity:()=>{hovered=null;schedule();},
    onSelect:(elements,additive)=>{window.focus?.();send({type:'artifact:targets',targets:elements.map(targetFor).filter(target=>target.selector),additive});},
    onError:message=>send({type:'artifact:edit-error',message})});
  /** Send plain anchor data; no content from the artifact becomes workspace markup. */
  function choose(event) {
    const element = event.target instanceof Element ? event.target : null;
    if (!element || element === overlay || overlay?.contains(element)) return;
    const selector = selectorFor(element);
    if (!selector) return;
    if (draft && draft.selector !== selector) { send({type: 'artifact:edit-error', message: 'Send or cancel the edit first.'}); return; }
    window.focus?.();
    const text = (element instanceof HTMLElement ? element.innerText : element.textContent) || '';
    const quote = text.replace(/\s+/g, ' ').trim().slice(0, 1000);
    const label = element.getAttribute('aria-label') || element.getAttribute('alt') || element.getAttribute('title') || quote || element.localName;
    send({
      type: 'artifact:target',
      target: {kind: 'element', selector, quote, label: label.slice(0, 500), image:element.localName==='img',viewport: {width: innerWidth, height: innerHeight}},
      additive: !!(event.metaKey || event.ctrlKey || event.shiftKey)
    });
  }

  for (const name of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'auxclick', 'contextmenu', 'touchstart', 'touchend']) {
    window.addEventListener(name, event => {
      if (!selecting || name==='contextmenu'&&event.target.closest?.('input,textarea,select,[contenteditable]')) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (name === 'click') choose(event);
      if (name === 'contextmenu') {
        const selected=selectors.find(selector=>{const element=selectedElement(selector);return element===event.target||element?.contains(event.target);});
        if(!selected)choose({target:event.target});
        send({type:'artifact:contextmenu',selector:selected||selectorFor(event.target),x:event.clientX,y:event.clientY,viewport:{width:innerWidth,height:innerHeight}});
      }
    }, {capture: true, passive: false});
  }
  window.addEventListener('keydown', event => {
    if (!selecting || event.isComposing || event.target.closest?.('input,textarea,select,[contenteditable]') || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();event.stopImmediatePropagation();choose(event);
  }, true);
  // Let authored controls and native dialogs consume Escape before leaving Studio's view.
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !event.defaultPrevented && !document.querySelector('dialog[open],:popover-open')) send({type: 'artifact:escape'});
  });
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
    if(message.type==='artifact:selection-export'&&typeof message.request_id==='string'&&Array.isArray(message.selectors)&&message.selectors.length>0&&message.selectors.length<=100&&message.selectors.every(value=>typeof value==='string'&&value.length<=2000)){
      window.ImageExport.snapshot(document,{selectors:message.selectors}).then(value=>send({type:'artifact:selection-export-data',request_id:message.request_id,...value})).catch(error=>send({type:'artifact:selection-export-error',request_id:message.request_id,message:error.message}));
    }else if (message.type === 'artifact:inspect' && typeof message.selector === 'string' && message.selector.length <= 2000) {
      if (draft && draft.selector !== message.selector) send({type: 'artifact:edit-error', message: 'Send or cancel the edit first.'});
      else inspect(message.selector, message.request_id);
    } else if (message.type === 'artifact:edit-preview' && typeof message.selector === 'string' && message.selector.length <= 2000) {
      try { previewEdit(message.selector, message.values, message.request_id); } catch (error) { send({type: 'artifact:edit-error', request_id: message.request_id, message: error.message}); }
    } else if (message.type === 'artifact:edit-cancel') { cancelEdit();
    } else if (message.type === 'artifact:edit-accept') { const selector = draft?.selector; draft = null; if (selector) inspect(selector);
    } else if (message.type === 'artifact:select' && typeof message.enabled === 'boolean') {
      selecting = message.enabled;
      if (!selecting) {hovered = null;marquee.cancel();}
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
