const {test}=require('node:test');
const assert=require('node:assert/strict');
const {interactiveDocument,interactiveMessage}=require('../runtime/presentation.js');

test('custom HTML is JSON encoded inside nested opaque sandboxes',()=>{
  const html=interactiveDocument('<script>custom()</script><button onclick="go()">Go</button>',{token:'check'});
  assert.match(html,/allow-scripts/);
  assert.doesNotMatch(html,/allow-same-origin|allow-top-navigation|allow-popups|allow-forms/);
  assert.match(html,/connect-src 'none'/);
  assert.match(html,/frame-src about:/);
  assert.match(html,/\\u003cscript>/);
  assert.match(html,/slideStudioCapture/);
});

test('status messages require the exact frame, opaque origin, token and known type',()=>{
  const source={},frame={contentWindow:source};
  const valid={source,origin:'null',data:{type:'interactive-ready',token:'correct'}};
  assert.equal(interactiveMessage(valid,frame,'correct'),true);
  for(const changed of [{source:{}},{origin:'https://test.invalid'},{data:{type:'interactive-ready',token:'wrong'}},{data:{type:'navigate',token:'correct'}}]){
    assert.equal(interactiveMessage({...valid,...changed},frame,'correct'),false);
  }
});
