/* Copy only the messages already visible in the selected conversation. */
((root) => {
  'use strict';
  const safeText = value => String(value || '').replace(/https?:\/\/[^\s<>)]*(?:preview|token|bearer)[^\s<>)]*/gi, '[link omitted]');
  function format(messages) {
    return messages.map(message => {
      const name = safeText(message.name || (message.author === 'user' ? 'You' : 'Agent'));
      const text = safeText(message.message || message.text || '');
      const files = (message.files || []).map(file => safeText(file.name || file.filename || '')).filter(Boolean);
      return [name + ':', text, ...files.map(file => 'Attachment: ' + file)].filter(Boolean).join('\n');
    }).join('\n\n');
  }
  async function copy({messages, clipboard, current, notify}) {
    const text = format(messages);
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
