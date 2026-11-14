/* Copy only the messages already visible in the selected conversation. */
((root) => {
  'use strict';
  const safeText = value => String(value || '').replace(/(?:https?:\/\/[^\s<>()]+)?\/_preview\/[^\s<>()]+/gi, '[preview link omitted]');
  function format(messages, title = '') {
    const content = messages.map(message => {
      const name = safeText(message.name || (message.author === 'user' ? 'You' : 'Agent'));
      const text = safeText(message.message || message.text || '');
      const files = (message.files || []).map(file => safeText(file.name || file.filename || '')).filter(Boolean);
      const target = safeText(message.copyTarget || '');
      const summary = (message.copySummary || []).map(safeText);
      return [name + ':', target ? 'Target: ' + target : '', text, ...summary, ...files.map(file => {
        const reference = /^(.+) - (?:project|artifact|presentation|slide) reference\.(?:json|html)$/.exec(file);
        return reference ? 'Reference: ' + reference[1] : 'Attachment: ' + file;
      })].filter(Boolean).join('\n');
    }).join('\n\n');
    return content && title ? safeText(title) + '\n\n' + content : content;
  }
  async function copy({messages, title, clipboard, current, notify}) {
    if (!current()) return;
    const text = format(messages, title);
    if (!text) { if (current()) notify('No messages to copy.'); return; }
    try {
      if (!clipboard?.writeText) throw new Error('Clipboard unavailable');
      await clipboard.writeText(text);
      if (current()) notify('Conversation copied.');
    } catch {
      if (current()) notify('Copy unavailable. Allow clipboard access or select and copy the conversation text.', true);
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {format, copy};
  else root.StudioChatCopy = {format, copy};
})(typeof window === 'undefined' ? {} : window);
