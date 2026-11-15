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

  /** Keep UI-only disclosure state; no message text or hidden ancestors are cached. */
  function create() {
    const contexts = new Map(), views = new WeakMap();
    function capture(list) {
      if (!list) return;
      const state = views.get(list);
      for (const details of list.querySelectorAll('.chat-replies')) {
        const value = state?.get(details.dataset.chatThread);
        if (value) { value.open = details.open; if (details.open) value.seen = Number(details.dataset.replyCount); }
      }
      const active = list.ownerDocument.activeElement;
      return list.contains(active) ? active.closest('[data-chat-focus]')?.dataset.chatFocus : undefined;
    }
    function mount(list, messages, {context, focus, name = message => message.name || (message.author === 'user' ? 'You' : 'Agent')} = {}) {
      if (!list) return;
      const children = [...list.children], rows = children.filter(node => node.tagName === 'ARTICLE');
      if (rows.length !== messages.length) return;
      const {keys, parents, roots, replies} = group(messages), doc = list.ownerDocument;
      // Channel changes can render an empty loading state before their read completes.
      const old = contexts.get(context) || new Map(), saved = new Map(old);
      roots.forEach((rootIndex, index) => {
        if (rootIndex === index && keys[index]) {
          saved.delete(keys[index]);
          saved.set(keys[index], old.get(keys[index]) || {open: null, seen: 0});
        }
      });
      views.set(list, saved);
      contexts.delete(context); contexts.set(context, saved);
      if (contexts.size > 40) contexts.delete(contexts.keys().next().value);
      const element = (tag, className, text) => {
        const node = doc.createElement(tag); node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
      };
      rows.forEach((row, index) => {
        row.dataset.chatMessage = keys[index] || `unlinked:${index}`;
        const parent = parents[index];
        if (parent === null) return;
        const original = messages[parent], quote = element('button', 'chat-reply-origin');
        quote.type = 'button'; quote.dataset.chatFocus = `quote:${keys[index]}`;
        quote.setAttribute('aria-label', `Go to message from ${name(original)}`);
        quote.append(element('span', 'chat-reply-label', `Reply to ${name(original)}`),
          element('span', 'chat-reply-excerpt', String(original.message || original.text || 'Attachment').replace(/\s+/g, ' ').slice(0, 160)));
        quote.addEventListener('click', () => {
          const target = rows[parent], disclosure = target.closest('.chat-replies');
          if (disclosure) disclosure.open = true;
          target.tabIndex = -1; target.focus({preventScroll: true});
          target.scrollIntoView({block: 'nearest'});
        });
        row.insertBefore(quote, row.querySelector(':scope > header, :scope > .comment-header, :scope > .hub-message-head')?.nextSibling || row.firstChild);
      });
      const replacements = new Map();
      for (const [index, indices] of replies) {
        const wrapper = element('section', 'chat-thread'), details = element('details', 'chat-replies');
        details.dataset.chatThread = keys[index];
        details.dataset.replyCount = indices.length;
        const summary = element('summary', 'chat-replies-toggle', `${indices.length} ${indices.length === 1 ? 'reply' : 'replies'}`);
        summary.dataset.chatFocus = `thread:${keys[index]}`;
        const needsAnswer = indices.some(item => rows[item].querySelector('[data-question-state="open"]') || rows[item].matches('[data-question-state="open"]'));
        const previous = old.get(keys[index]), state = {open: previous?.open ?? true, seen: previous ? previous.seen : indices.length};
        details.open = state.open; if (state.open) state.seen = indices.length;
        saved.set(keys[index], state);
        const unread = element('span', 'chat-thread-new'); summary.append(unread);
        function syncUnread() { const count = Math.max(0, indices.length - state.seen); unread.hidden = !count; unread.textContent = count ? `${count} new` : ''; }
        syncUnread();
        if (needsAnswer) summary.append(element('span', 'chat-thread-attention', 'Answer requested'));
        details.addEventListener('toggle', () => {
          if (!details.isConnected) return;
          state.open = details.open; if (details.open) state.seen = indices.length;
          syncUnread();
        });
        const body = element('div', 'chat-thread-replies');
        body.append(...indices.map(item => rows[item])); details.append(summary, body);
        wrapper.append(rows[index], details); replacements.set(rows[index], wrapper);
      }
      // Retain date separators only for messages that remain at the top level.
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
      if (focus) [...list.querySelectorAll('[data-chat-focus]')].find(node => node.dataset.chatFocus === focus)?.focus({preventScroll: true});
    }
    return {mount, capture};
  }

  const api = {render, group, create};
  if (typeof module !== 'undefined') module.exports = api; else root.StudioChatTimeline = api;
})(typeof window === 'undefined' ? {} : window);
