const test=require('node:test');
const assert=require('node:assert/strict');
const {format,copy}=require('../web/chat-copy.js');
test('copy formats visible text and attachment titles without preview links',()=>{
  const text=format([{name:'Alice',message:'Review **this** https://preview.example.test/_preview/secret/file.png',files:[{name:'Reference.pdf',url:'https://preview.example.test/private'},{name:'Fictional slide - slide reference.html'}]},{author:'agent',message:'Ready'}]);
  assert.match(text,/Alice:\nReview \*\*this\*\*/);assert.match(text,/Attachment: Reference.pdf/);assert.match(text,/Reference: Fictional slide/);assert.match(text,/Agent:\nReady/);assert.doesNotMatch(text,/secret|private/);
});
test('ordinary authored URLs are preserved',()=>{assert.match(format([{message:'https://example.test/token-economics https://example.test/preview'}]),/https:\/\/example.test\/token-economics https:\/\/example.test\/preview/);});
test('stale context makes no clipboard write',async()=>{let calls=0;await copy({messages:[{message:'text'}],clipboard:{writeText:async()=>calls++},current:()=>false,notify:()=>{throw Error('stale notification');}});assert.equal(calls,0);});
test('clipboard failure is actionable and never retries after context changes',async()=>{
  let active=true,calls=0;const notices=[];
  await copy({messages:[{message:'text'}],clipboard:{writeText:async()=>{calls++;active=false;throw Error();}},current:()=>active,notify:(...args)=>notices.push(args)});
  assert.equal(calls,1);assert.deepEqual(notices,[]);
  active=true;await copy({messages:[{message:'text'}],clipboard:null,current:()=>active,notify:(...args)=>notices.push(args)});
  assert.match(notices[0][0],/clipboard access/i);assert.equal(notices[0][1],true);
});

test('copy includes only supplied visible title and target labels, never target metadata',()=>{
  const text=format([{name:'You',message:'Delete this element.',copyTarget:'Revenue chart',target:{html:'<secret>',quote:'private note',preview_url:'https://preview.test/_preview/token'}}], 'Quarterly plan · Cover');
  assert.match(text,/^Quarterly plan · Cover\n\nYou:\nTarget: Revenue chart\nDelete this element\./);
  assert.doesNotMatch(text,/secret|private note|_preview|token/);
  assert.equal(format([], 'Empty conversation'), '');
});
