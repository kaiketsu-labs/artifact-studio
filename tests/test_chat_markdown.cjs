const {test} = require('node:test');
const assert = require('node:assert/strict');
const marked = require('../web/vendor/marked-18.1.2.js');
const {create} = require('../web/chat-markdown.js');
const markdown = create(marked);

test('messages support common agent Markdown including tables and fenced source', () => {
  const html = markdown.render('## Result\n\n**Ready** and *reviewed*.\n\n- First\n- `Second`\n\n> Context\n\n```html\n<div>Example</div>\n```\n\n| Check | Status |\n| --- | --- |\n| Chat | Ready |');
  for (const tag of ['<h2>', '<strong>', '<em>', '<ul>', '<code>', '<blockquote>', '<pre>', '<table>']) {
    assert.ok(html.includes(tag), tag);
  }
  assert.match(html, /&lt;div&gt;Example&lt;\/div&gt;/);
});

test('raw HTML remains literal and Markdown images never make automatic requests', () => {
  const html = markdown.render('<script>alert(1)</script>\n\n<img src="https://example.com/pixel" onerror="alert(1)">\n\n![Chart](https://example.com/chart.svg)');
  assert.ok(!/<(?:script|img|iframe|style)\b/i.test(html));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /<span class="chat-image-alt">Chart<\/span>/);
});

test('only ordinary web and mail links become clickable, with safe attributes', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,hello', 'vbscript:msgbox(1)', 'file:///etc/passwd']) {
    assert.ok(!markdown.render(`[Unsafe](${url})`).includes('<a '), url);
  }
  const html = markdown.render('[Docs](https://example.com/?a=1&b=2 "A & B") [Mail](mailto:team@example.com)');
  assert.match(html, /href="https:\/\/example.com\/\?a=1&amp;b=2"/);
  assert.match(html, /title="A &amp; B"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /href="mailto:team@example.com"/);
});

test('chat rendering does not change the shared rules-editor Markdown parser', () => {
  markdown.render('<b>Literal in chat</b>');
  assert.match(marked.parse('<b>Rich rules</b>'), /<b>Rich rules<\/b>/);
  assert.equal(markdown.render(null), '');
});
