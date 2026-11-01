const {test}=require('node:test');
const assert=require('node:assert/strict');
const {pending,status}=require('../web/slide-questions.js');

test('only an answer on the original slide resolves a pending question',()=>{
  const question={event_id:11,slide_id:'cover',author:'agent',question:{options:['One','Two'],cancelled:false}};
  const unrelated={event_id:12,slide_id:'other',author:'user',reply_to:11,released:true};
  assert.equal(status(question,[question,unrelated]),'open');
  assert.deepEqual(pending([question,unrelated]),[question]);
  const answer={...unrelated,slide_id:'cover'};
  assert.equal(status(question,[question,answer]),'answered');
  assert.deepEqual(pending([question,answer]),[]);
});

test('queued answers and cancelled questions are distinct from delivered answers',()=>{
  const question={event_id:11,slide_id:'cover',author:'agent',question:{options:[],cancelled:false}};
  const queued={event_id:12,slide_id:'cover',author:'user',reply_to:11,released:false};
  assert.equal(status(question,[question,queued]),'queued');
  assert.deepEqual(pending([question,queued]),[]);
  const cancelled={...question,question:{...question.question,cancelled:true}};
  assert.equal(status(cancelled,[cancelled]),'cancelled');
  assert.equal(status(cancelled,[cancelled,queued]),'queued');
});
