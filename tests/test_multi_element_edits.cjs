const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../web/multi-element-edits.js');

function fixture(){
  const view={getComputedStyle:()=>({position:'absolute',zIndex:'auto'})};
  class Node {
    constructor(){this.childNodes=[];this.parentNode=null;this.attrs={};this.isConnected=true;this.style={setProperty:(name,value)=>{this.attrs.style=`${name}:${value}`;}};this.listeners={};this.ownerDocument=doc;}
    get children(){return this.childNodes;} get parentElement(){return this.parentNode;}
    get nextElementSibling(){const a=this.parentNode.children;return a[a.indexOf(this)+1]||null;}
    get previousElementSibling(){const a=this.parentNode.children;return a[a.indexOf(this)-1]||null;}
    append(...nodes){for(const node of nodes)this.appendChild(node);}
    appendChild(node){if(node.parentNode)node.parentNode.childNodes.splice(node.parentNode.childNodes.indexOf(node),1);this.childNodes.push(node);node.parentNode=this;return node;}
    insertBefore(node,ref){if(node.parentNode)node.parentNode.childNodes.splice(node.parentNode.childNodes.indexOf(node),1);this.childNodes.splice(this.childNodes.indexOf(ref),0,node);node.parentNode=this;}
    matches(){return false;}
    contains(node){return node===this||this.childNodes.some(child=>child.contains(node));}
    setAttribute(name,value){this.attrs[name]=value;} getAttribute(name){return this.attrs[name]??null;} removeAttribute(name){delete this.attrs[name];}
    addEventListener(name,handler){this.listeners[name]=handler;}
  }
  const doc={defaultView:view,createElement:()=>new Node()};doc.body=new Node();const parent=new Node(),a=new Node(),b=new Node(),c=new Node();doc.body.append(parent);parent.append(a,b,c);let selected=[a,c],sent=[];
  const editor=create({doc,getElements:()=>selected,getContext:()=>({hash:'original'}),onSend:async payload=>sent.push(payload)});
  return {parent,a,b,c,editor,sent,select:value=>{selected=value;}};
}
test('delete, undo, redo and cancel preserve node identity and original styles',()=>{
  const {a,c,editor}=fixture();a.setAttribute('style','color:red');
  assert.equal(editor.preview('delete'),true);assert.equal(a.getAttribute('style'),'visibility:hidden');
  assert.equal(editor.undo(),true);assert.equal(a.getAttribute('style'),'color:red');assert.equal(editor.state().canRedo,true);
  assert.equal(editor.redo(),true);assert.equal(c.getAttribute('style'),'visibility:hidden');
  assert.equal(editor.cancel(),true);assert.equal(a.getAttribute('style'),'color:red');assert.equal(c.getAttribute('style'),null);
});
test('arrange preserves elements and order on cancel; send is one group feedback',async()=>{
  const {parent,a,b,c,editor,sent}=fixture();assert.equal(editor.preview('front'),true);assert.deepEqual(parent.children,[b,a,c]);
  assert.equal(editor.undo(),true);assert.deepEqual(parent.children,[a,b,c]);assert.equal(editor.redo(),true);
  assert.equal(await editor.send(),true);assert.equal(sent.length,1);assert.deepEqual(JSON.parse(sent[0].message),{action:'edit_selection',operations:[{action:'front'}]});assert.deepEqual(sent[0].context,{hash:'original'});assert.equal(editor.state().sent,true);assert.equal(editor.state().dirty,false);assert.deepEqual(parent.children,[b,a,c]);editor.onSelectionChange();assert.deepEqual(parent.children,[a,b,c]);assert.equal(editor.state().sent,false);
});
test('nested selections are normalized and alignment remains an explicit request',async()=>{
  const {a,b,editor,sent,select}=fixture();a.append(b);select([a,b]);assert.equal(editor.preview('left'),true);
  assert.equal(await editor.send(),true);assert.deepEqual(JSON.parse(sent[0].message).operations,[{action:'left'}]);
});

test('failed send keeps an identical request for retry and delete is not duplicated',async()=>{
  let calls=[];const f=fixture();
  const editor=create({doc:f.a.ownerDocument,getElements:()=>[f.a],getContext:()=>({hash:'original',request_id:'same'}),onSend:async payload=>{calls.push(JSON.stringify(payload));if(calls.length===1)throw Error('offline');}});
  assert.equal(editor.preview('delete'),true);assert.equal(editor.preview('delete'),false);
  assert.equal(await editor.send(),false);assert.equal(editor.state().dirty,true);assert.equal(await editor.send(),true);assert.equal(calls[0],calls[1]);
  editor.onSelectionChange();assert.equal(f.a.getAttribute('style'),null);
});
test('history is bounded',()=>{const {editor}=fixture();for(let i=0;i<45;i++)assert.equal(editor.preview('left'),true);for(let i=0;i<40;i++)assert.equal(editor.undo(),true);assert.equal(editor.undo(),false);});
test('unsupported arrangement is a truthful undoable request without a DOM preview',async()=>{
  const {a,b,c,parent,editor,sent}=fixture();a.ownerDocument.defaultView.getComputedStyle=()=>({position:'static',zIndex:'auto'});
  assert.equal(editor.preview('front'),true);assert.deepEqual(parent.children,[a,b,c]);
  assert.equal(editor.toolbar.childNodes[0].textContent,'Unsent selection request · arrangement is not previewed');
  assert.equal(editor.undo(),true);assert.equal(editor.redo(),true);assert.equal(await editor.send(),true);
  assert.deepEqual(JSON.parse(sent[0].message).operations,[{action:'front',request_only:true}]);
});
test('readable messages recognize only group operations',()=>{
  const {readableMessage}=require('../web/multi-element-edits.js');
  assert.equal(readableMessage(JSON.stringify({action:'edit_selection',operations:[{action:'delete'},{action:'left'}]})),'Delete selection · Align left');
  assert.equal(readableMessage('ordinary feedback'),null);
  assert.equal(readableMessage('{"action":"edit_selection","operations":[{"action":"unknown"}]}'),null);
});
