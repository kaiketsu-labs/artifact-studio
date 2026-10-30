const {test}=require('node:test');
const assert=require('node:assert/strict');
const {objectLabel,location}=require('../web/objects.js');

test('object labels normalize and truncate descriptive text',()=>{
  assert.equal(objectLabel('Heading','  Quarterly\n results  '),'Heading · Quarterly results');
  assert.equal(objectLabel('Image','', 'top left'),'Image · top left');
  assert.equal(objectLabel('Text','x'.repeat(80)).length,'Text · '.length+70);
});

test('location describes the object center on the slide',()=>{
  assert.equal(location(0.1,0.1),'top left');
  assert.equal(location(0.5,0.5),'middle center');
  assert.equal(location(0.9,0.9),'bottom right');
});
