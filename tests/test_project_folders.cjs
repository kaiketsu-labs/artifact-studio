const test = require('node:test');
const assert = require('node:assert/strict');
const {create, MIME} = require('../web/project-folders.js');

function fixture() {
  const handlers = {}, classes = () => ({values:new Set(), add(x){this.values.add(x)}, remove(x){this.values.delete(x)}});
  const card = {dataset:{dragArtifact:'artifact-1'}, classList:classes(), closest(selector){return selector.includes('.hub-card') ? this : null}};
  const folder = {dataset:{projectFolder:'project-2'}, classList:classes(), closest(selector){return selector.includes('.hub-sidebar') ? this : null}, contains(target){return target === this}};
  const content = {addEventListener(name, fn){handlers[name]=fn}, removeEventListener(name){delete handlers[name]}, querySelectorAll(){return [card]}};
  const artifacts = [{id:'artifact-1',project_id:'project-1'}], projects = [{id:'project-1'},{id:'project-2'}];
  const calls = [], errors = [];
  const api = create({content,getArtifacts:()=>artifacts,getProjects:()=>projects,onMove:async (...args)=>{calls.push(args)},onError:error=>errors.push(error)});
  const data = new Map(), transfer = {types:[],setData(type,value){data.set(type,value);this.types.push(type)},getData(type){return data.get(type) || ''}};
  const event = (target, dt=transfer) => ({target,dataTransfer:dt,prevented:false,preventDefault(){this.prevented=true}});
  return {handlers,card,folder,artifacts,projects,calls,errors,api,transfer,event};
}

test('moves a registered artifact to a different registered folder', async () => {
  const f=fixture(); f.handlers.dragstart(f.event(f.card));
  const over=f.event(f.folder); f.handlers.dragover(over);
  assert.equal(over.prevented,true); assert.equal(f.folder.classList.values.has('is-drop-target'),true);
  const drop=f.event(f.folder); await f.handlers.drop(drop);
  assert.equal(drop.prevented,true); assert.deepEqual(f.calls,[['artifact-1','project-2']]);
  assert.equal(f.folder.classList.values.has('is-drop-target'),false);
  f.handlers.dragend(); assert.equal(f.card.classList.values.has('is-dragging'),false);
});

test('rejects external transfers, forged payloads, unknown folders and same-project drops', async () => {
  const f=fixture();
  assert.equal((await f.handlers.drop(f.event(f.folder))),undefined);
  f.handlers.dragstart(f.event(f.card)); f.transfer.types.push('Files');
  let over=f.event(f.folder); f.handlers.dragover(over); assert.equal(over.prevented,false);
  f.transfer.types.pop(); f.folder.dataset.projectFolder='project-1';
  over=f.event(f.folder); f.handlers.dragover(over); assert.equal(over.prevented,false);
  f.folder.dataset.projectFolder='missing'; over=f.event(f.folder); f.handlers.dragover(over); assert.equal(over.prevented,false);
  f.folder.dataset.projectFolder='project-2'; f.transfer.setData(MIME,'foreign');
  await f.handlers.drop(f.event(f.folder)); assert.deepEqual(f.calls,[]);
});

test('prevents repeated drops while moving and reports errors', async () => {
  const f=fixture(); let reject;
  f.api.destroy();
  create({content:{addEventListener(n,h){f.handlers[n]=h},removeEventListener(){},querySelectorAll(){return []}},getArtifacts:()=>f.artifacts,getProjects:()=>f.projects,onMove:()=>new Promise((_,r)=>{reject=r}),onError:e=>f.errors.push(e)});
  f.handlers.dragstart(f.event(f.card)); const pending=f.handlers.drop(f.event(f.folder));
  f.handlers.dragstart(f.event(f.card)); const duplicate=f.event(f.folder); await f.handlers.drop(duplicate);
  assert.equal(duplicate.prevented,false); reject(new Error('conflict')); await pending;
  assert.equal(f.errors[0].message,'conflict');
});
