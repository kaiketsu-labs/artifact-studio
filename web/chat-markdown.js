/* Render message Markdown without granting raw HTML or automatic image requests. */
((root) => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  function safeHref(value) {
    try {
      const url = new URL(value, 'https://slide-studio.invalid/');
      return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? value : '';
    } catch { return ''; }
  }

  function create(markdown) {
    const renderer = new markdown.Renderer();
    renderer.html = token => escape(token.text);
    renderer.image = token => `<span class="chat-image-alt">${escape(token.text || 'Image')}</span>`;
    renderer.link = function(token) {
      const label = this.parser.parseInline(token.tokens);
      const href = safeHref(token.href);
      if (!href) return label;
      const title = token.title ? ` title="${escape(token.title)}"` : '';
      return `<a href="${escape(href)}"${title} target="_blank" rel="noopener noreferrer">${label}</a>`;
    };
    const parser = new markdown.Marked({renderer, gfm: true, breaks: true});
    return {
      render(value) {
        const text = String(value ?? '');
        try { return parser.parse(text, {async: false}); }
        catch { return `<p>${escape(text).replace(/\n/g, '<br>')}</p>`; }
      }
    };
  }

  if (typeof module !== 'undefined') module.exports = {create};
  else root.SlideChatMarkdown = create(root.marked);
})(typeof window === 'undefined' ? {} : window);
