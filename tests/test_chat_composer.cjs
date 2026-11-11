const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../web/chat-images.js');
const {create,selectionError,sizeLabel}=require('../web/chat-composer.js');

function node(){return {innerHTML:'',textContent:'',disabled:false,classList:{toggle(){},add(){},remove(){}},setAttribute(){},contains(){return false;},events:{},addEventListener(name,fn){this.events[name]=fn;},click(){this.clicked=true;}};}
function fixture(){let current='deck/slide-1',changed=0;const elements=Object.fromEntries(['form','input','attachButton','fileInput','filesList','error','status'].map(name=>[name,node()]));elements.filesList.ownerDocument=node();const chat=create({...elements,key:()=>current,escape:value=>String(value).replace(/[<>"&]/g,''),svg:()=>'',onChange:()=>changed++});return {chat,elements,setKey:value=>current=value,changes:()=>changed};}

test('limits reject the entire new selection and allow exact boundaries',()=>{
  assert.equal(selectionError(Array.from({length:5},()=>({size:10000000}))), '');
  assert.match(selectionError(Array.from({length:6},()=>({size:1}))),/5 files/);
  assert.match(selectionError([{size:25000001}]),/25 MB/);
  assert.match(selectionError([{size:25000000},{size:25000000},{size:1}]),/50 MB/);
  assert.equal(sizeLabel(25000000),'25.0 MB');
});

test('files stay with their slide and can be removed without changing other drafts',()=>{
  const {chat,elements,setKey}=fixture();
  assert.equal(chat.add([{name:'first.pdf',size:50}]),true);
  assert.equal(chat.hasFiles(),true);assert.match(elements.filesList.innerHTML,/first.pdf/);
  setKey('deck/slide-2');chat.sync();assert.equal(chat.hasFiles(),false);
  chat.add([{name:'second.png',size:60}]);
  chat.clear('deck/slide-1');assert.equal(chat.hasFiles(),true);assert.equal(chat.hasFiles('deck/slide-1'),false);
  chat.sync({disabled:true});assert.equal(chat.add([{name:'blocked.pdf',size:1}]),false);
  chat.sync({disabled:false});
  elements.filesList.events.click({target:{closest:()=>({dataset:{chatRemove:'0'}})}});
  assert.equal(chat.hasDrafts(),false);
});

test('read progress and retry preserve the original files after failures',async()=>{
  const previous=global.FileReader;
  const {chat,elements}=fixture();
  chat.add([{name:'reference.pdf',size:5}]);
  global.FileReader=class{readAsDataURL(){this.onerror();}};
  try{
    await assert.rejects(chat.payload(),/Could not read reference.pdf/);
    assert.equal(chat.hasFiles(),true);assert.match(elements.error.textContent,/attach it again/);
    global.FileReader=class{readAsDataURL(){this.result='data:application/pdf;base64,c2xpZGU=';this.onprogress({lengthComputable:true,loaded:5,total:5});this.onload();}};
    assert.deepEqual(await chat.payload(),[{name:'reference.pdf',data_base64:'c2xpZGU='}]);
    assert.match(elements.status.textContent,/Sending 1 file/);
    chat.idle();assert.equal(elements.status.textContent,'');
  }finally{global.FileReader=previous;}
});

test('history exposes only local conversation download routes',()=>{
  const {chat}=fixture();
  const file={name:'reference.pdf',size:5,download_url:'/api/decks/demo/feedback/1/files/0'};
  assert.match(chat.sentFiles({files:[file]}),/download/);
  const scopedURL='/api/workspaces/default/artifacts/demo/chat/2/files/0';
  assert.ok(chat.sentFiles({scoped:true,files:[{...file,download_url:scopedURL}]}).includes(`href="${scopedURL}" download`));
  assert.equal(chat.sentFiles({files:[{...file,download_url:'https://external.example/private.pdf'}]}),'');
});

function paste(elements,files,text=''){
  let prevented=false;
  elements.input.events.paste({clipboardData:{files,getData:type=>type==='text/plain'?text:''},preventDefault(){prevented=true;}});
  return prevented;
}

test('mixed clipboard content retains native text insertion even when files are rejected',()=>{
  const {chat,elements}=fixture();
  assert.equal(paste(elements,[{name:'diagram.png',size:50}],'Keep these labels'),false);
  assert.equal(chat.hasFiles(),true);
  assert.match(elements.filesList.innerHTML,/diagram.png/);
  assert.equal(paste(elements,[{name:'oversized.pdf',size:25000001}],'Keep this text too'),false);
  assert.match(elements.error.textContent,/25 MB/);
  assert.doesNotMatch(elements.filesList.innerHTML,/oversized.pdf/);
  assert.equal(paste(elements,[{name:'clip.png',size:50}]),true);
  assert.match(elements.filesList.innerHTML,/clip.png/);
});

test('errors and progress remain scoped to the original slide',()=>{
  const {chat,elements,setKey}=fixture();
  chat.error('Slide one problem');chat.busy('Slide one sending');
  setKey('deck/slide-2');chat.sync();
  assert.equal(elements.error.textContent,'');assert.equal(elements.status.textContent,'');
  chat.error('Slide two problem');chat.busy('Slide two sending');
  chat.idle('deck/slide-1');chat.error('Original request failed','deck/slide-1');
  assert.equal(elements.error.textContent,'Slide two problem');
  assert.equal(elements.status.textContent,'Slide two sending');
  chat.clear('deck/slide-1');
  assert.equal(elements.error.textContent,'Slide two problem');
  setKey('deck/slide-1');chat.sync();
  assert.equal(elements.error.textContent,'');assert.equal(elements.status.textContent,'');
});

test('asynchronous file progress and failures cannot leak into another slide',async()=>{
  const previous=global.FileReader,readers=[];
  const {chat,elements,setKey}=fixture();
  global.FileReader=class{readAsDataURL(){readers.push(this);}};
  try{
    chat.add([{name:'original.pdf',size:50}]);
    const reading=chat.payload('deck/slide-1');
    assert.match(elements.status.textContent,/original.pdf/);
    setKey('deck/slide-2');chat.sync();
    assert.equal(elements.status.textContent,'');
    assert.equal(elements.attachButton.disabled,false);
    readers[0].onprogress({lengthComputable:true,loaded:25,total:50});
    assert.equal(elements.status.textContent,'');
    readers[0].onerror();
    await assert.rejects(reading,/Could not read original.pdf/);
    assert.equal(elements.error.textContent,'');assert.equal(elements.status.textContent,'');
    setKey('deck/slide-1');chat.sync();
    assert.match(elements.error.textContent,/Could not read original.pdf/);
    assert.equal(elements.status.textContent,'');
    assert.equal(elements.attachButton.disabled,false);
    assert.equal(chat.hasFiles(),true);
  }finally{global.FileReader=previous;}
});

test('successful background encoding retains its sending state on the original slide',async()=>{
  const previous=global.FileReader,readers=[];
  const {chat,elements,setKey}=fixture();
  global.FileReader=class{readAsDataURL(){readers.push(this);}};
  try{
    chat.add([{name:'original.pdf',size:50}]);
    const reading=chat.payload('deck/slide-1');
    setKey('deck/slide-2');chat.sync();chat.busy('Second slide state');
    readers[0].result='data:application/pdf;base64,c2xpZGU=';readers[0].onload();
    assert.deepEqual(await reading,[{name:'original.pdf',data_base64:'c2xpZGU='}]);
    assert.equal(elements.status.textContent,'Second slide state');
    setKey('deck/slide-1');chat.sync();assert.match(elements.status.textContent,/Sending 1 file/);
    chat.idle('deck/slide-1');assert.equal(elements.status.textContent,'');
  }finally{global.FileReader=previous;}
});
