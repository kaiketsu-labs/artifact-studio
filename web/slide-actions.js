/* Recoverable structural changes, separate from slide content and review state. */
(() => {
  'use strict';
  /** Bind slide commands while retaining the same request after uncertain delivery. */
  function create({read,selection,prepare,apply,request,notify,onBusy}){
    let busy=false,pending=null,lastDeletion=null;
    const $=id=>document.getElementById(id);
    const {button:iconButton}=window.SlideIcons;
    iconButton($('add-slide'),'slideAdd','New slide');
    $('add-slide').setAttribute('aria-keyshortcuts','Control+m Meta+Shift+n');
    iconButton($('deleted-slides'),'trash','Deleted slides');
    iconButton($('close-deleted-slides'),'close','Close');

    function update(){
      const state=read();
      $('add-slide').disabled=busy||!state||state.slides.length>=100;
      $('deleted-slides').disabled=busy||!state;
    }
    async function ready(context){
      if(busy)return false;
      busy=true;onBusy(true);update();
      try{return await prepare(context);}
      catch(error){notify(error.message,null,true);return false;}
      finally{busy=false;onBusy(false);update();}
    }
    async function execute(command){
      if(busy)return false;
      if(read()?.id!==command.deckId){notify('The presentation changed. Open the original presentation to retry this slide action.',()=>execute(command),true);return false;}
      pending=command;
      busy=true;onBusy(true);update();
      try{
        const result=await request(command.url,command.payload);
        const applied=await apply(result,command);
        pending=null;
        if(applied){
          if(result.action==='delete'){
            lastDeletion={deckId:command.deckId,id:result.operation_id};
            notify(`${result.deleted_ids.length===1?'Slide':'Slides'} deleted.`,()=>restore(command.deckId,result.operation_id));
          }else{
            lastDeletion=null;
            notify(({add:'Slide added.',duplicate:'Slides duplicated.',restore:'Slides restored.'})[result.action]);
          }
        }
        return !!applied;
      }catch(error){
        // A lost response may follow a committed write; retries must reuse its token.
        if(error.status&&error.status<500)pending=null;
        notify(error.message,()=>execute(command),true);return false;
      }finally{busy=false;onBusy(false);update();}
    }
    async function run(action){
      if(busy)return;
      const state=read();if(!state)return;
      if(pending){
        if(pending.deckId===state.id&&pending.payload.action===action){await execute(pending);return;}
        notify('Finish the previous slide action first.',()=>execute(pending),true);return;
      }
      if(!await ready({action,ids:action==='add'?[state.activeId]:selection()}))return;
      const current=read(),ids=action==='add'?[current.activeId]:selection();
      if(action==='delete'&&ids.length===current.slides.length){notify('Keep at least one slide.',null,true);return;}
      const payload={action,slide_ids:ids,expected_slide_ids:current.slides.map(slide=>slide.id),expected_hashes:Object.fromEntries(current.slides.filter(slide=>ids.includes(slide.id)).map(slide=>[slide.id,slide.expected_hash])),request_id:crypto.randomUUID()};
      pending={deckId:current.id,url:`/api/decks/${encodeURIComponent(current.id)}/slide-actions`,payload,activeId:current.activeId,previousIds:payload.expected_slide_ids};
      await execute(pending);
    }
    async function restore(deckId,deletionId){
      if(busy)return false;
      if(read()?.id!==deckId){notify('The presentation changed. Open the original presentation to restore its slides.',null,true);return false;}
      if(pending){notify('Finish the previous slide action first.',()=>execute(pending),true);return false;}
      if(!await ready({action:'restore',ids:[]}))return false;
      if(read()?.id!==deckId){notify('The presentation changed. Open the original presentation to restore its slides.',null,true);return false;}
      pending={deckId,url:`/api/decks/${encodeURIComponent(deckId)}/restore-slides`,payload:{deletion_id:deletionId,request_id:crypto.randomUUID()},activeId:read()?.activeId};
      const done=await execute(pending);
      if(done&&$('deleted-slides-dialog').open)await showDeleted();
      return done;
    }
    async function undo(){
      if(!lastDeletion||lastDeletion.deckId!==read()?.id)return false;
      return await restore(lastDeletion.deckId,lastDeletion.id);
    }
    async function showDeleted(){
      const state=read();if(!state||busy)return;
      const dialog=$('deleted-slides-dialog'),list=$('deleted-slides-list');
      if(!dialog.open)dialog.showModal();
      list.replaceChildren();$('deleted-slides-error').textContent='';
      try{
        const items=await request(`/api/decks/${encodeURIComponent(state.id)}/deleted-slides`);
        if(read()?.id!==state.id||!dialog.open)return;
        if(!items.length){const empty=document.createElement('p');empty.textContent='No deleted slides.';list.append(empty);}
        for(const item of items){
          const row=document.createElement('div');row.className='deleted-slide-row';
          const label=document.createElement('span');label.textContent=item.slides.map(slide=>slide.title).join(' · ');
          const button=document.createElement('button');button.type='button';iconButton(button,'restore','Restore');
          button.addEventListener('click',async()=>{button.disabled=true;await restore(state.id,item.operation_id);button.disabled=false;});
          row.append(label,button);list.append(row);
        }
      }catch(error){$('deleted-slides-error').textContent=error.message;}
    }
    $('add-slide').addEventListener('click',()=>run('add'));
    $('deleted-slides').addEventListener('click',showDeleted);
    $('close-deleted-slides').addEventListener('click',()=>$('deleted-slides-dialog').close());
    update();
    return {run,update,undo,restore,clearUndo:()=>{lastDeletion=null;},isBusy:()=>busy};
  }
  window.SlideActions={create};
})();
