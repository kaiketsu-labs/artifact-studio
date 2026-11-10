const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create,imageFile,downloadURL}=require('../web/chat-images.js');

function fixture(){
  let next=0;const created=[],revoked=[];
  const document={addEventListener(){}};
  const view={URL:{createObjectURL(file){created.push(file);return `blob:local-${++next}`;},revokeObjectURL(url){revoked.push(url);}}};
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return {images:create({document,view,escape,svg:()=>''}),created,revoked};
}

test('draft images reuse URLs across renders and release only discarded file references',()=>{
  const {images,created,revoked}=fixture(),first={name:'chart.png'},second={name:'photo.JPG'};
  const initial=images.draft(first);
  assert.match(initial,/blob:local-1/);assert.equal(images.draft(first),initial);
  images.draft(second);assert.equal(created.length,2);
  images.retain([first,second]);assert.deepEqual(revoked,[]);
  images.retain([second]);assert.deepEqual(revoked,['blob:local-1']);
  images.retain([]);assert.deepEqual(revoked,['blob:local-1','blob:local-2']);
});

test('sent thumbnails accept only exact local conversation routes and escape image names',()=>{
  const {images}=fixture();
  for(const url of ['/api/decks/demo/feedback/2/files/0','/api/artifacts/artifact-demo/feedback/2/files/0','/api/workspaces/default/artifacts/artifact-demo/chat/2/files/0']){
    const rendered=images.sent({name:'<chart>.png',download_url:url});
    assert.ok(rendered.includes(url+'?preview=true'));
    assert.match(rendered,/&lt;chart&gt;\.png/);assert.doesNotMatch(rendered,/<chart>/);
    for(const invalid of ['https://other.example'+url,'//other.example'+url,url+'/extra',url+'?other=1']){
      assert.equal(downloadURL(invalid),'');
      assert.equal(images.sent({name:'chart.png',download_url:invalid}),'');
    }
  }
  assert.equal(downloadURL('/api/workspaces/default/artifacts/%2e%2e/chat/2/files/0'),'');
  assert.equal(downloadURL('/api/workspaces/default/artifacts/artifact-demo/feedback/2/files/0'),'');
  assert.equal(images.sent({name:'context.pdf',download_url:'/api/decks/demo/feedback/2/files/0'}),'');
  assert.equal(imageFile({name:'active.svg'}),false);
  assert.equal(imageFile({name:'page.html'}),false);
});
