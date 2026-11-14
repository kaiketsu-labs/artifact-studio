const test=require('node:test');
const assert=require('node:assert/strict');
const {format,copy}=require('../web/chat-copy.js');
test('copy formats visible text and attachment titles without preview links',()=>{
  const text=format([{name:'Alice',message:'Review **this** https://preview.example.test/a?token=secret',files:[{name:'Reference.pdf',url:'https://preview.example.test/private'}]},{author:'agent',message:'Ready'}]);
  assert.match(text,/Alice:\nReview \*\*this\*\*/);assert.match(text,/Attachment: Reference.pdf/);assert.match(text,/Agent:\nReady/);assert.doesNotMatch(text,/secret|private/);
});
test('clipboard failure is actionable and never retries after context changes',async()=>{
  let active=true,calls=0;const notices=[];
  await copy({messages:[{message:'text'}],clipboard:{writeText:async()=>{calls++;active=false;throw Error();}},current:()=>active,notify:(...args)=>notices.push(args)});
  assert.equal(calls,1);assert.deepEqual(notices,[]);
  active=true;await copy({messages:[{message:'text'}],clipboard:null,current:()=>active,notify:(...args)=>notices.push(args)});
  assert.match(notices[0][0],/clipboard access/i);assert.equal(notices[0][1],true);
});
