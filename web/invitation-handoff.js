/* The token stays in this tab until verified-recipient acceptance. */
(()=>{
  'use strict';
  const KEY='artifact-studio-invitation-handoff-v1',TTL=30*60*1000;
  const $=id=>document.getElementById(id),mode=document.documentElement.dataset.mode;
  const validToken=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{43}$/.test(value);
  const clear=()=>sessionStorage.removeItem(KEY);
  const message=text=>{$('status').textContent=text;};
  function pending(){
    const item=JSON.parse(sessionStorage.getItem(KEY)||'null');
    if(!item||!validToken(item.token)||!Number.isSafeInteger(item.created)||item.created>Date.now()+5000||Date.now()-item.created>TTL){clear();return null;}
    return item;
  }
  function safeDestination(value){
    return typeof value==='string'&&(
      /^\/(?:workspaces\/[a-f0-9]{24}\/)?(?:\?(?:project|artifact)=[a-z0-9][a-z0-9-]{0,99})?$/.test(value)||
      /^\/\?workspace=[a-f0-9]{24}(?:&(?:project|artifact)=[a-z0-9][a-z0-9-]{0,99})?$/.test(value));
  }
  async function request(path,body){
    const response=await fetch(path,{credentials:'same-origin',cache:'no-store',redirect:'error',
      ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    if(!response.ok){const error=new Error('Invitation unavailable');error.status=response.status;throw error;}
    return response.json();
  }
  $('cancel').addEventListener('click',()=>{try{clear();}catch{}});
  function prepareLanding(){
    const values=new URLSearchParams(location.hash.slice(1)),token=values.get('invite');
    history.replaceState(null,'','/_invitation');
    clear();$('continue').hidden=true;$('continue').onclick=null;
    if(values.size!==1||!validToken(token)){message('This invitation link is incomplete. Ask the owner for a new link.');return;}
    sessionStorage.setItem(KEY,JSON.stringify({token,created:Date.now()}));
    message('Sign in with the email that received this invitation.');$('continue').hidden=false;
    $('continue').onclick=()=>{
      try{if(!pending())throw Error();location.replace('/invitation');}
      catch{message('This sign-in handoff expired. Open the original invitation link again.');$('continue').hidden=true;}
    };
  }
  async function start(){
    if(mode==='unavailable'){
      clear();message('This invitation is unavailable for this account. Open the original link after signing in with the invited email.');return;
    }
    if(mode==='landing'){
      prepareLanding();
      window.addEventListener('hashchange',()=>{
        try{prepareLanding();}catch{message('Could not retain this invitation. Reopen the original link.');$('continue').hidden=true;}
      });return;
    }
    const invitation=pending();
    if(!invitation){message('Open your original invitation link to continue.');return;}
    const session=await request('/api/together/session');
    if(typeof session.email!=='string'||!session.email){clear();throw Error();}
    $('account').textContent='Signed in as '+session.email;$('account').hidden=false;
    message('Join to open the project or presentation shared with you.');$('join').hidden=false;
    $('join').addEventListener('click',async()=>{
      $('join').disabled=true;
      try{
        const current=pending();if(!current||current.token!==invitation.token)throw Object.assign(Error(),{status:404});
        const result=await request('/api/invitations/accept',{token:current.token});
        clear();
        if(!safeDestination(result.destination))throw Object.assign(Error(),{status:404});
        location.replace(result.destination);
      }catch(error){
        if([400,401,403,404].includes(error.status)){
          clear();$('join').hidden=true;message('This invitation is unavailable for this account. Check the invited email or ask the owner for a new link.');
        }else{message('Could not join yet. Check your connection and try again.');}
      }finally{$('join').disabled=false;}
    });
  }
  start().catch(()=>{message('Could not continue securely. Reopen the original invitation link and try again.');$('continue').hidden=true;$('join').hidden=true;});
})();
