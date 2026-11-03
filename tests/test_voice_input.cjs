const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createRecorder,insertText}=require('../web/voice-input.js');

const result=(text,final=true)=>Object.assign([{transcript:text}],{isFinal:final});
function fixture(){
  const instances=[],states=[],timers=new Map();let nextTimer=0,idle=0;
  class Recognition{
    constructor(){instances.push(this);this.starts=0;this.stops=0;this.aborts=0;}
    start(){this.starts++;this.onstart?.();}
    stop(){this.stops++;}
    abort(){this.aborts++;}
    emit(...results){this.onresult?.({resultIndex:0,results});}
    end(){this.onend?.();}
    error(error){this.onerror?.({error});}
  }
  const recorder=createRecorder({Recognition,language:'de-DE',onState:(handle,state)=>states.push({handle,...state}),onIdle:()=>idle++,
    setTimer:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearTimer:id=>timers.delete(id)});
  return {recorder,instances,states,timers,get idle(){return idle;},drain(){const due=[...timers.values()];timers.clear();for(const fn of due)fn();}};
}
function field(context={target:'heading'}){
  const received=[];
  const handle={adapter:{capture:()=>context,insert:(text,captured)=>{received.push({text,context:structuredClone(captured)});return true;},available:()=>true}};
  return {handle,received,context};
}

test('recognition is explicit, browser language is set, and only final new results insert',()=>{
  const check=fixture(),target=field();
  assert.equal(check.instances.length,0);
  assert.equal(check.recorder.start(target.handle),true);
  const speech=check.instances[0];
  assert.equal(speech.starts,1);assert.equal(speech.continuous,true);assert.equal(speech.interimResults,true);
  assert.equal(speech.maxAlternatives,1);assert.equal(speech.lang,'de-DE');
  speech.emit(result('make this blu',false));assert.deepEqual(target.received,[]);
  assert.equal(check.states.at(-1).interim,'make this blu');
  speech.emit(result('Make this blue.'));speech.emit(result('Make this blue.'),result('Use a larger label.'));
  assert.deepEqual(target.received.map(item=>item.text),['Make this blue.','Use a larger label.']);
});

test('selection boundaries freeze snapshots and drain late finals to the previous target',()=>{
  const check=fixture(),target=field({target:{kind:'group',elements:[{selector:'#one'},{selector:'#two'}]}});
  check.recorder.start(target.handle);const first=check.instances[0];
  first.emit(result('Align these',false));
  target.context.target={selector:'#three'};
  assert.equal(check.recorder.boundary(target.handle),true);
  assert.equal(first.stops,1);assert.equal(check.recorder.isActive(target.handle),true);
  first.emit(result('Align these two.'));
  first.end();assert.equal(check.instances.length,2);
  check.instances[1].emit(result('Make this third label bold.'));
  assert.deepEqual(target.received,[
    {text:'Align these two.',context:{target:{kind:'group',elements:[{selector:'#one'},{selector:'#two'}]}}},
    {text:'Make this third label bold.',context:{target:{selector:'#three'}}}
  ]);
});

test('rapid selection changes keep the newest pending context and retire old callbacks',()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);
  const first=check.instances[0],late=first.onresult;
  target.context.target='body';check.recorder.boundary(target.handle);
  target.context.target='chart';check.recorder.boundary(target.handle);
  first.end();assert.equal(check.instances.length,2);
  late({results:[result('Stale result')],resultIndex:0});
  check.instances[1].emit(result('Change chart.'));
  assert.deepEqual(target.received,[{text:'Change chart.',context:{target:'chart'}}]);
});

test('explicit stop remains active through the final drain, then resolves without restarting',async()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);
  const speech=check.instances[0];let settled=false;
  const stopped=check.recorder.stop().then(completed=>{settled=true;return completed;});
  assert.equal(settled,false);assert.equal(check.recorder.isActive(),true);
  speech.emit(result('Final words.'));speech.end();assert.equal(await stopped,true);
  assert.equal(check.recorder.isActive(),false);assert.equal(check.instances.length,1);
  assert.equal(target.received[0].text,'Final words.');assert.equal(check.idle,1);
});

test('cancel, errors, timeouts and unfinished speech never authorize a pending send',async()=>{
  for(const ending of ['cancel','error','timeout','unfinished','stop-error']){
    const check=fixture(),target=field();check.recorder.start(target.handle);
    const speech=check.instances[0];speech.emit(result('Confirmed draft.'));
    if(ending==='unfinished')speech.emit(result('Confirmed draft.'),result('Unfinished words',false));
    if(ending==='stop-error')speech.stop=()=>{throw Error('Recognition failed to stop');};
    const stopped=check.recorder.stop();
    if(ending==='cancel')check.recorder.cancel();
    if(ending==='error')speech.error('network');
    if(ending==='timeout')check.drain();
    if(ending==='unfinished')speech.end();
    assert.equal(await stopped,false,ending);
    assert.equal(check.recorder.isActive(),false,ending);
    assert.deepEqual(target.received.map(item=>item.text),['Confirmed draft.']);
  }
  const check=fixture();assert.equal(await check.recorder.stop(),false);
});

test('a stop timeout aborts and never promotes unfinished interim text',async()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);
  const speech=check.instances[0],late=speech.onresult;
  speech.emit(result('Unconfirmed words',false));const stopped=check.recorder.stop();check.drain();await stopped;
  assert.equal(speech.aborts,1);assert.equal(check.recorder.isActive(),false);
  assert.match(check.states.at(-1).message,/unfinished speech was not inserted/i);
  late({results:[result('Late words')],resultIndex:0});assert.deepEqual(target.received,[]);
});

test('selection changes cannot restart recording once an explicit stop is draining',async()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);
  const stopped=check.recorder.stop();target.context.target='body';
  assert.equal(check.recorder.boundary(target.handle),false);
  check.instances[0].emit(result('Original heading.'));check.instances[0].end();
  assert.equal(await stopped,true);assert.equal(check.instances.length,1);
  assert.deepEqual(target.received,[{text:'Original heading.',context:{target:'heading'}}]);
});

test('a newly requested field prevents a pending send from authorizing submission',async()=>{
  const check=fixture(),first=field(),second=field({target:'notes'});
  check.recorder.start(first.handle);const stopped=check.recorder.stop();
  check.recorder.start(second.handle);check.instances[0].end();
  assert.equal(await stopped,false);assert.equal(check.recorder.isActive(second.handle),true);
  check.recorder.cancel();
});

test('permission and service errors release the microphone and cancel a queued restart',()=>{
  for(const error of ['not-allowed','service-not-allowed','audio-capture','network','no-speech']){
    const check=fixture(),target=field();check.recorder.start(target.handle);
    const speech=check.instances[0],ended=speech.onend;
    target.context.target='another';check.recorder.boundary(target.handle);speech.error(error);ended();check.drain();
    assert.equal(check.instances.length,1);assert.equal(check.recorder.isActive(),false);assert.equal(speech.aborts,1);
    assert.equal(check.states.at(-1).phase,'error');assert.equal(check.timers.size,0);
  }
});

test('natural recognition end requires another explicit start instead of a restart loop',()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);check.instances[0].end();
  assert.equal(check.recorder.isActive(),false);assert.equal(check.instances.length,1);
  check.recorder.start(target.handle);assert.equal(check.instances.length,2);
});

test('switching fields drains the old field before starting the new field',()=>{
  const check=fixture(),first=field({field:'notes'}),second=field({field:'brief'});
  check.recorder.start(first.handle);const notes=check.instances[0];
  check.recorder.start(second.handle);notes.emit(result('Notes only.'));notes.end();
  check.instances[1].emit(result('Brief only.'));
  assert.equal(first.received[0].context.field,'notes');assert.equal(second.received[0].context.field,'brief');
  check.recorder.cancel(first.handle);assert.equal(check.recorder.isActive(second.handle),true);
});

test('scoped stop does not stop another field and scoped cancellation keeps queued destinations',async()=>{
  const check=fixture(),first=field(),second=field({target:'other'});
  check.recorder.start(first.handle);check.recorder.start(second.handle);
  check.recorder.cancel(first.handle);assert.equal(check.instances.length,2);
  await check.recorder.stop(first.handle);assert.equal(check.recorder.isActive(second.handle),true);
  check.instances[1].emit(result('Still listening.'));assert.equal(second.received.length,1);
  check.recorder.cancel();assert.equal(check.recorder.isActive(),false);
});

test('custom availability can retain durable destinations while refusing invalid contexts',()=>{
  const check=fixture(),target=field({slide:'original'});let valid=true;
  target.handle.adapter.available=()=>valid;check.recorder.start(target.handle);
  check.instances[0].emit(result('Original slide.'));valid=false;
  check.instances[0].emit(result('Original slide.'),result('Do not redirect this.'));
  assert.equal(target.received.length,1);assert.equal(check.recorder.isActive(),false);
  assert.match(check.states.at(-1).message,/original field changed/i);
});

test('failed insertion retains recognized text in an actionable status and stops',()=>{
  const check=fixture(),target=field();target.handle.adapter.insert=()=>false;
  check.recorder.start(target.handle);check.instances[0].emit(result('Text to recover.'));
  assert.equal(check.recorder.isActive(),false);assert.match(check.states.at(-1).message,/Text to recover\./);
  assert.equal(check.states.at(-1).phase,'error');
});

test('unsupported browsers and failed captures never start the microphone',()=>{
  const states=[],unsupported=createRecorder({Recognition:null,onState:(_handle,state)=>states.push(state)}),target=field();
  assert.equal(unsupported.start(target.handle),false);assert.equal(unsupported.isActive(),false);
  assert.equal(states.at(-1).phase,'unavailable');
  const check=fixture();target.handle.adapter.capture=()=>{throw Error('No current field');};
  assert.equal(check.recorder.start(target.handle),false);assert.equal(check.instances.length,0);
});

test('destroy retires callbacks and prevents any later start',()=>{
  const check=fixture(),target=field();check.recorder.start(target.handle);
  const speech=check.instances[0],late=speech.onresult;check.recorder.destroy();
  late({results:[result('Old input')],resultIndex:0});assert.deepEqual(target.received,[]);
  assert.equal(check.recorder.start(target.handle),false);assert.equal(speech.aborts,1);
});

function textField(value,start=value.length,end=start,maxLength=-1){
  const events=[],input={value,selectionStart:start,selectionEnd:end,maxLength,isConnected:true,disabled:false,readOnly:false,
    dispatchEvent:event=>events.push(event),setRangeText(text,from,to){this.value=this.value.slice(0,from)+text+this.value.slice(to);this.selectionStart=this.selectionEnd=from+text.length;}};
  const document={activeElement:null};input.ownerDocument={defaultView:{Event}};
  return {input,document,events,context:{value,start,end}};
}

test('default insertion preserves a retained replacement range and dispatches normal input events',()=>{
  const check=textField('Keep old ending',5,8);
  assert.equal(insertText(check.input,'new',check.context,check.document),true);
  assert.equal(check.input.value,'Keep new ending');assert.equal(check.events[0].type,'input');assert.equal(check.events[0].bubbles,true);
  assert.equal(insertText(check.input,'more',check.context,check.document),true);
  assert.equal(check.input.value,'Keep new more ending');
});

test('newer typing and caret movements are respected without replacing text from a stale range',()=>{
  const check=textField('Earlier');check.document.activeElement=check.input;
  check.input.value='Earlier typed';check.input.selectionStart=check.input.selectionEnd=13;
  assert.equal(insertText(check.input,'spoken',check.context,check.document),true);
  assert.equal(check.input.value,'Earlier typed spoken');
});

test('a changed inactive field or a disabled/removed field rejects late generic speech',()=>{
  for(const change of [input=>input.value='Another document',input=>input.disabled=true,input=>input.readOnly=true,input=>input.isConnected=false]){
    const check=textField('Original');change(check.input);const before=check.input.value;
    assert.equal(insertText(check.input,'Late speech',check.context,check.document),false);
    assert.equal(check.input.value,before);assert.equal(check.events.length,0);
  }
});

test('field character limits reject a whole phrase instead of silently truncating it',()=>{
  const check=textField('Note',4,4,8);
  assert.equal(insertText(check.input,'long phrase',check.context,check.document),false);
  assert.equal(check.input.value,'Note');assert.deepEqual(check.events,[]);
});
