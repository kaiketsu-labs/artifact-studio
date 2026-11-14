const {test}=require('node:test');
const assert=require('node:assert/strict');
const {displayEdit,create}=require('../web/native-conversation.js');
const {format}=require('../web/chat-copy.js');

function request(action,values,prefix='Update element properties.') {
  return {author:'owner',person:'owner',assigned:'agent',target:{selector:'#title',label:'Heading'},
    message:prefix+'\n\nEdit details:\n'+JSON.stringify({action,values})};
}

test('known editor changes have readable text without mutating the original message',()=>{
  const record=request('replace_text',{text:'A clearer story <script>example</script>'});
  const before=JSON.stringify(record),display=displayEdit(record);
  assert.equal(display.message,'Element change requested.');
  assert.deepEqual(display.edit_summary,['Text: A clearer story <script>example</script>']);
  assert.equal(JSON.stringify(record),before);
  const copied=format([{name:'You',message:display.message,copySummary:display.edit_summary,copyTarget:'Heading'}]);
  assert.match(copied,/Text: A clearer story/);
  assert.doesNotMatch(copied,/Edit details:|replace_text|"values"/);
});

test('position and formatting preserve the meaning of the original values',()=>{
  const display=displayEdit(request('update',{x:.25,y:.5,canvas_reference:'artifact_document',canvas_width:1200,canvas_height:600,
    position_reference:'visual_top_left',font_size:24,font_size_unit:'px',font_weight:'bold',color:'#123456'}));
  assert.deepEqual(display.edit_summary,['Text size: 24 px','Text weight: bold','Text color: #123456',
    'Position from left: 300 px in the document','Position from top: 300 px in the document']);
  assert.deepEqual(displayEdit(request('rotate',{rotation_degrees:15,rotation_reference:'original_element_local',
    rotation_origin:'existing_transform_origin'},'Rotate 15° clockwise from original.')).edit_summary,
    ['Rotate 15° from the original']);
  assert.equal(displayEdit(request('delete',{},'Delete this element.')).message,'Delete this element.');
});

test('ordinary authored content, unknown schemas and malformed details stay intact',()=>{
  const ordinary=request('replace_text',{text:'Example'});
  for(const record of [
    {...ordinary,author:'agent'}, {...ordinary,assigned:''}, {...ordinary,target:null},
    {...ordinary,message:'Explain this JSON: {"action":"replace_text"}'},
    request('new_action',{text:'Future action'}), request('style',{custom_property:'Keep this source'}),
    request('replace_text',{text:123}), request('move',{x:'0.25'}), request('style',{text:['array']}),
    {...ordinary,message:'Keep this\n\nEdit details:\n{"action":'},
    {...ordinary,message:'Keep this\n\nEdit details:\n{"action":"style","values":{"__proto__":"literal"}}'},
  ]) {
    assert.deepEqual(displayEdit(record),{message:record.message,edit_summary:[]});
  }
});

test('authored prefixes never resolve to inherited object properties',()=>{
  for(const prefix of ['__proto__','constructor','toString']) {
    const record=request('replace_text',{text:'Literal requested text'},prefix);
    const display=displayEdit(record);
    assert.equal(display.message,prefix);
    assert.equal(typeof display.message,'string');
    assert.equal(display.original_request,record.message);
  }
});

test('long replacement fallback displays all text while the agent retains exact wire details',async()=>{
  const text='"\\\n'.repeat(2000),sent=[];
  const chat=create({api:async(path,body)=>{sent.push({path,body});return {id:1};}});
  await chat.send('artifact',{message:'Replace text.',requestId:'stable-request',contentHash:'original-hash',
    target:{kind:'element',selector:'#title',label:'Heading'},editIntent:{action:'replace_text',values:{text}},
    assigned:'agent',forAgent:true});
  const body=sent[0].body;
  assert.equal(body.request_id,'stable-request');
  assert.equal(body.content_hash,'original-hash');
  assert.match(body.message,/\nReplacement text:\n/);
  assert.equal(displayEdit({...body,author:'owner',person:'owner'}).edit_summary[0],'Text: '+text);
  assert.equal(body.message,'Replace text.\n\nEdit details:\n{"action":"replace_text","values":{}}\nReplacement text:\n'+text);
});
