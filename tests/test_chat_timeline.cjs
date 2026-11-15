const test = require('node:test');
const assert = require('node:assert/strict');
const {group, render} = require('../web/chat-timeline.js');

test('stored links group replies once, including replies to replies', () => {
  const result = group([{id:1}, {id:2}, {id:3,reply_to:1}, {id:4,reply_to:3}]);
  assert.deepEqual([...result.replies], [[0,[2,3]]]);
  assert.deepEqual(result.parents, [null,null,0,2]);
});

test('local and scoped IDs and channels cannot match each other', () => {
  const result = group([
    {event_id:1}, {event_id:-1,scoped:true},
    {event_id:2,reply_to:1}, {event_id:-2,reply_to:-1,scoped:true},
    {id:1,channel:'private'}, {id:3,channel:'shared',reply_to:1},
  ]);
  assert.deepEqual([...result.replies], [[0,[2]], [1,[3]]]);
  assert.equal(result.parents[5], null);
});

test('orphans, duplicate IDs and cycles remain visible without text matching', () => {
  const messages = [
    {id:1,message:'Same words'}, {id:2,message:'Same words',reply_to:99},
    {id:3,reply_to:4}, {id:4,reply_to:3}, {id:5,reply_to:5},
    {id:6}, {id:6}, {id:7,reply_to:6},
  ];
  const result = group(messages);
  assert.equal(result.replies.size, 0);
  assert.deepEqual([...result.roots.keys()].sort((a,b)=>a-b), messages.map((_,index)=>index));
});

test('long reply chains resolve without recursive stack growth', () => {
  const messages = Array.from({length:20000}, (_,index)=>({id:index+1,reply_to:index||null}));
  const result = group(messages);
  assert.equal(result.replies.get(0).length,19999);
});

test('date separators retain conversation order and invalid dates are safe', () => {
  const messages = [{date:'2026-11-15T10:00:00Z',text:'First'},{date:'invalid',text:'Second'},{date:'2026-11-15T11:00:00Z',text:'Third'}];
  const result=render(messages,item=>item.date,item=>item.text,()=> 'Day');
  assert.deepEqual(result,['Day','First','Second','Third']);
});
