/* Preserve conversation order while displaying dates in the reader’s time zone. */
((root) => {
  'use strict';
  const dayFormat = new Intl.DateTimeFormat(undefined, {dateStyle: 'medium'});

  function render(messages, timestamp, messageMarkup, dayMarkup) {
    let previousDay;
    return messages.flatMap(message => {
      const value = timestamp(message);
      const date = value == null ? null : new Date(value);
      const day = date && !Number.isNaN(date.getTime()) ? dayFormat.format(date) : null;
      const line = messageMarkup(message);
      if (!day || day === previousDay) return [line];
      previousDay = day;
      return [dayMarkup(day), line];
    });
  }

  const identity = message => message.event_id ?? message.id;
  const namespace = message => `${message.scoped ? 'scoped' : 'local'}:${message.channel || ''}`;
  const keyFor = (message, value) => value == null ? null : `${namespace(message)}:${typeof value}:${value}`;

  /** Resolve only supplied, visible messages. Duplicate IDs and cycles never hide messages. */
  function group(messages) {
    const keys = messages.map(message => keyFor(message, identity(message))), byKey = new Map();
    keys.forEach((key, index) => { if (key !== null) byKey.set(key, byKey.has(key) ? null : index); });
    const parents = messages.map((message, index) => {
      const parent = byKey.get(keyFor(message, message.reply_to));
      return parent == null || parent === index || byKey.get(keys[index]) !== index ? null : parent;
    });
    const roots = new Map();
    messages.forEach((_, index) => {
      if (roots.has(index)) return;
      const path = [], seen = new Set(); let current = index;
      while (!roots.has(current) && !seen.has(current)) {
        path.push(current); seen.add(current);
        if (parents[current] === null) { roots.set(current, current); break; }
        current = parents[current];
      }
      if (seen.has(current) && !roots.has(current)) {
        // Break this malformed chain without dropping any of its messages.
        path.forEach(item => { parents[item] = null; roots.set(item, item); });
      } else path.forEach(item => roots.set(item, roots.get(current)));
    });
    const replies = new Map();
    messages.forEach((_, index) => {
      const rootIndex = roots.get(index);
      if (rootIndex !== index) {
        if (!replies.has(rootIndex)) replies.set(rootIndex, []);
        replies.get(rootIndex).push(index);
      }
    });
    return {keys, parents, roots, replies};
  }

  /** UI state contains only disclosure flags and the user's unsent drafts, never parent text. */
  function create() {
    const contexts = new Map(), views = new WeakMap();
    const initial = () => ({open: true, seen: 0, draft: '', composing: false,
      editorOpen: false, pending: false, requestId: null, error: '', start: 0, end: 0});
    function capture(list) {
      if (!list) return;
      const view = views.get(list);
      for (const details of list.querySelectorAll('.chat-replies')) {
        const state = view?.states.get(details.dataset.chatThread);
        if (state) { state.open = details.open; if (details.open) state.seen = Number(details.dataset.replyCount); }
      }
      const active = list.ownerDocument.activeElement;
      if (active?.matches('.chat-thread-input') && list.contains(active)) {
        const state = view?.states.get(active.dataset.chatEditor);
        if (state) { state.start = active.selectionStart; state.end = active.selectionEnd; state.direction = active.selectionDirection; }
      }
      const key = list.contains(active) ? active.closest('[data-chat-focus]')?.dataset.chatFocus : undefined;
      return key ? {context: view?.context, key} : undefined;
    }
    function isComposing(list) {
      const states = views.get(list)?.states;
      return [...list.querySelectorAll('[data-chat-editor]')].some(field => states?.get(field.dataset.chatEditor)?.composing);
    }
    function mount(list, messages, {context, focus, name = message => message.name || (message.author === 'user' ? 'You' : 'Agent'),
      onReply, canReply = () => Boolean(onReply), replyLabel = () => 'Reply in thread', replyHint = () => ''} = {}) {
      if (!list) return;
      const children = [...list.children], rows = children.filter(node => node.tagName === 'ARTICLE');
      if (rows.length !== messages.length) return;
      const {keys, parents, roots, replies} = group(messages), doc = list.ownerDocument;
      const occurrences = new Map();
      keys.forEach(key => occurrences.set(key, (occurrences.get(key) || 0) + 1));
      const saved = contexts.get(context) || new Map();
      views.set(list, {context, states: saved});
      contexts.delete(context); contexts.set(context, saved);
      if (contexts.size > 40) contexts.delete(contexts.keys().next().value);
      const element = (tag, className, text) => {
        const node = doc.createElement(tag); node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
      };
      const icon = () => {
        const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true');
        const path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'M5 14H3V4h14v10H9l-4 3v-3Z'); svg.append(path); return svg;
      };
      rows.forEach((row, index) => {
        row.dataset.chatMessage = keys[index] || `unlinked:${index}`;
        const parent = parents[index];
        // The root is already directly above its replies. Only a nested reply needs context.
        if (parent === null || parent === roots.get(index)) return;
        const original = messages[parent], link = element('button', 'chat-reply-origin', `↳ To ${name(original)}`);
        link.type = 'button'; link.dataset.chatFocus = `quote:${keys[index]}`;
        link.setAttribute('aria-label', `Go to reply from ${name(original)}`);
        link.addEventListener('click', () => {
          const target = rows[parent], disclosure = target.closest('.chat-replies');
          if (disclosure) disclosure.open = true;
          target.tabIndex = -1; target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest'});
        });
        row.insertBefore(link, row.querySelector(':scope > header, :scope > .comment-header, :scope > .hub-message-head')?.nextSibling || row.firstChild);
      });
      const replacements = new Map();
      roots.forEach((rootIndex, index) => {
        if (rootIndex !== index) return;
        const indices = replies.get(index) || [], key = keys[index];
        const writable = Boolean(key && occurrences.get(key) === 1 && onReply && canReply(messages[index]));
        if (!indices.length && !writable) return;
        const previous = saved.get(key), state = previous || initial();
        if (!previous) state.seen = indices.length;
        saved.delete(key); saved.set(key, state);
        const wrapper = element('section', 'chat-thread');
        wrapper.append(rows[index]); replacements.set(rows[index], wrapper);
        let details, body;
        if (indices.length) {
          details = element('details', 'chat-replies'); details.dataset.chatThread = key;
          details.dataset.replyCount = indices.length; details.open = state.open;
          if (state.open) state.seen = indices.length;
          const summary = element('summary', 'chat-replies-toggle');
          summary.dataset.chatFocus = `thread:${key}`;
          summary.append(element('span', 'chat-thread-count', `${indices.length} ${indices.length === 1 ? 'reply' : 'replies'}`),
            element('span', 'chat-thread-collapse', 'Hide'), element('span', 'chat-thread-expand', 'Show'));
          const unread = element('span', 'chat-thread-new'); summary.append(unread);
          const syncUnread = () => { const count = Math.max(0, indices.length - state.seen); unread.hidden = !count; unread.textContent = count ? `${count} new` : ''; };
          syncUnread();
          if (indices.some(item => rows[item].querySelector('[data-question-state="open"]') || rows[item].matches('[data-question-state="open"]'))) {
            summary.append(element('span', 'chat-thread-attention', 'Answer requested'));
          }
          details.addEventListener('toggle', () => {
            if (!details.isConnected) return;
            state.open = details.open; if (details.open) state.seen = indices.length; syncUnread();
          });
          body = element('div', 'chat-thread-replies'); body.append(...indices.map(item => rows[item]));
          details.append(summary, body); wrapper.append(details);
        }
        if (!writable) return;
        const reply = element('button', 'chat-thread-reply'); reply.type = 'button';
        reply.append(icon(), doc.createTextNode(replyLabel(messages[index]) || 'Reply in thread'));
        reply.dataset.chatFocus = `reply:${key}`;
        reply.setAttribute('aria-label', `${reply.textContent} to ${name(messages[index])}`);
        wrapper.append(reply);
        const form = element('form', 'chat-thread-compose');
        form.dataset.chatComposer = key; form.hidden = !state.editorOpen;
        const label = element('label', 'chat-thread-compose-label', `Reply to ${name(messages[index])}`);
        const input = element('textarea', 'chat-thread-input'); input.rows = 2;
        input.dataset.chatEditor = key; input.dataset.chatFocus = `composer:${key}`;
        input.setAttribute('aria-label', `Reply in thread to ${name(messages[index])}`);
        input.placeholder = 'Write a reply…'; input.value = state.draft;
        const audience = replyHint(messages[index]);
        if (audience) label.firstChild.textContent = audience;
        label.append(input);
        const controls = element('div', 'chat-thread-compose-actions');
        const hint = element('span', 'chat-thread-compose-hint', 'Shift+Enter for a new line');
        const cancel = element('button', 'chat-thread-cancel', 'Close'); cancel.type = 'button';
        const send = element('button', 'chat-thread-send', 'Send reply'); send.type = 'submit';
        controls.append(hint, cancel, send);
        const error = element('p', 'chat-thread-error'); error.setAttribute('role', 'alert');
        form.append(error, label, controls);
        (body || wrapper).append(form);
        function update(revealError = false) {
          // A send can finish after polling replaced the DOM or the user changed channels.
          if (views.get(list)?.context !== context || views.get(list)?.states.get(key) !== state) return;
          const current = [...list.querySelectorAll('[data-chat-composer]')].find(node => node.dataset.chatComposer === key);
          if (!current) return;
          current.hidden = !state.editorOpen;
          const field = current.querySelector('textarea'), submit = current.querySelector('.chat-thread-send');
          if (field.value !== state.draft) field.value = state.draft;
          field.readOnly = state.pending; current.setAttribute('aria-busy', String(state.pending));
          submit.disabled = state.pending || !state.draft.trim(); submit.textContent = state.pending ? 'Sending…' : 'Send reply';
          const alert = current.querySelector('.chat-thread-error'); alert.textContent = state.error; alert.hidden = !state.error;
          if (revealError && state.error && current.contains(doc.activeElement)) alert.scrollIntoView({block: 'nearest'});
        }
        function saveSelection() { state.start = input.selectionStart; state.end = input.selectionEnd; state.direction = input.selectionDirection; }
        input.addEventListener('input', () => {
          if (state.draft !== input.value) { state.requestId = null; state.error = ''; }
          state.draft = input.value; state.edited = Date.now(); saveSelection(); update();
        });
        input.addEventListener('blur', () => { saveSelection(); state.composing = false; });
        input.addEventListener('compositionstart', () => { state.composing = true; });
        input.addEventListener('compositionend', () => { state.composing = false; });
        input.addEventListener('keydown', event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !state.composing && event.keyCode !== 229) {
            event.preventDefault(); form.requestSubmit();
          }
        });
        form.addEventListener('submit', async event => {
          event.preventDefault();
          if (state.pending || state.composing || !state.draft.trim()) return;
          state.pending = true; state.error = '';
          state.requestId ||= root.crypto?.randomUUID?.() || `thread-${Date.now()}-${Math.random().toString(36).slice(2)}`;
          saveSelection(); update();
          try {
            await onReply(messages[index], state.draft.trim(), state.requestId);
            state.draft = ''; state.requestId = null; state.start = state.end = 0;
          } catch (failure) {
            state.error = failure?.message || 'Reply could not be sent. Try again.';
          } finally { state.pending = false; update(true); }
        });
        reply.addEventListener('click', () => {
          state.editorOpen = true; if (details) { details.open = true; state.open = true; }
          form.hidden = false; input.focus(); input.setSelectionRange(state.start, state.end, state.direction);
          input.scrollIntoView({block: 'nearest'});
        });
        cancel.addEventListener('click', () => { state.editorOpen = false; form.hidden = true; reply.focus(); });
        // This initial render is detached; update() intentionally resolves only the mounted view.
        input.readOnly = state.pending; form.setAttribute('aria-busy', String(state.pending));
        send.disabled = state.pending || !state.draft.trim(); send.textContent = state.pending ? 'Sending…' : 'Send reply';
        error.textContent = state.error; error.hidden = !state.error;
      });
      const result = [], rowIndices = new Map(rows.map((row, index) => [row, index])); let day = null, previousDay = null;
      for (const child of children) {
        if (child.classList.contains('chat-day')) { day = child; continue; }
        const index = rowIndices.get(child);
        if (index !== undefined && roots.get(index) !== index) continue;
        if (day && day.textContent !== previousDay) { result.push(day); previousDay = day.textContent; }
        result.push(replacements.get(child) || child);
      }
      list.replaceChildren(...result);
      while (saved.size > 2000) saved.delete(saved.keys().next().value);
      // Keep only the most recently edited drafts when browsing very large histories.
      const drafts = [...saved.values()].filter(state => state.draft && !state.pending)
        .sort((a, b) => (b.edited || 0) - (a.edited || 0));
      drafts.slice(200).forEach(state => { state.draft = ''; state.requestId = null; state.error = ''; });
      if (focus?.context === context && focus?.key) {
        const target = [...list.querySelectorAll('[data-chat-focus]')].find(node => node.dataset.chatFocus === focus.key);
        if (target && !target.closest('[hidden]') && (target.matches('summary') || !target.closest('details') || target.closest('details').open)) {
          target.focus({preventScroll: true});
          if (target.matches('.chat-thread-input')) {
            const state = saved.get(target.dataset.chatEditor); target.setSelectionRange(state.start, state.end, state.direction);
          }
        }
      }
    }
    return {mount, capture, isComposing};
  }

  const api = {render, group, create};
  if (typeof module !== 'undefined') module.exports = api; else root.StudioChatTimeline = api;
})(typeof window === 'undefined' ? {} : window);
