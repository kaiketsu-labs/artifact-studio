/* Compact attachment previews shared by presentation and artifact conversations. */
((root) => {
  'use strict';
  const raster=/\.(?:png|jpe?g|webp|gif|avif|bmp|tiff?|ico)$/i;
  const imageFile=file=>raster.test(file.name || '');
  const fileRoute=/^\/api\/(?:(?:decks\/[a-z0-9-]+|artifacts\/[a-zA-Z0-9_-]+)\/feedback|workspaces\/[a-z0-9-]+\/artifacts\/[a-z0-9-]+\/chat)\/\d+\/files\/\d+$/;
  const downloadURL=value=>typeof value==='string'&&fileRoute.test(value)?value:'';
  function create({document:doc,view=root,escape,svg}) {
    const urls=new Map();let dialog=null,trigger=null;
    function local(file) {
      if(!urls.has(file)){
        try{urls.set(file,view.URL.createObjectURL(file));}catch{return '';}
      }
      return urls.get(file);
    }
    function tile(name,source,remove='',download='') {
      return `<span class="chat-image-tile"><button type="button" class="chat-image-open" data-chat-image="${escape(source)}" data-image-name="${escape(name)}" data-image-download="${escape(download)}" aria-label="Preview ${escape(name)}" title="${escape(name)}"><img src="${escape(source)}" alt="${escape(name)}" decoding="async" loading="lazy"></button><span class="chat-image-fallback" hidden>${download?`<a href="${escape(download)}" download>${escape(name)}</a>`:escape(name)}</span>${remove}</span>`;
    }
    doc.addEventListener('error',event=>{
      const image=event.target,tile=image.closest?.('.chat-image-tile');
      if(!tile || image.tagName!=='IMG')return;
      tile.classList.add('is-unavailable');tile.querySelector('.chat-image-open').hidden=true;tile.querySelector('.chat-image-fallback').hidden=false;
    },true);
    doc.addEventListener('click',event=>{
      const button=event.target.closest?.('[data-chat-image]');if(!button)return;
      const source=button.dataset.chatImage,download=downloadURL(button.dataset.imageDownload);
      if(![...urls.values()].includes(source) && (!download || source!==download+'?preview=true'))return;
      if(!dialog){
        dialog=doc.createElement('dialog');dialog.className='chat-image-dialog';
        dialog.addEventListener('keydown',event=>{
          event.stopPropagation();
          if(event.key!=='Tab')return;
          const controls=[...dialog.querySelectorAll('button,a[href]')];
          const current=controls.indexOf(doc.activeElement);
          const next=event.shiftKey?(current<=0?controls.length-1:current-1):(current+1)%controls.length;
          event.preventDefault();
          controls[next]?.focus();
        });
        dialog.addEventListener('click',event=>{if(event.target.closest('[data-image-close]') || event.target===dialog)dialog.close();});
        dialog.addEventListener('close',()=>{
          dialog.querySelector('img')?.removeAttribute('src');
          const equivalent=[...doc.querySelectorAll('[data-chat-image]')].find(node=>node.dataset.chatImage===trigger?.dataset.chatImage);
          (trigger?.isConnected?trigger:equivalent || doc.querySelector('#feedback-text,#hub-feedback-text'))?.focus({preventScroll:true});
        });
        doc.body.append(dialog);
      }
      trigger=button;
      const name=button.dataset.imageName;
      dialog.setAttribute('aria-label',`Image preview: ${name}`);
      dialog.innerHTML=`<header><span>${escape(name)}</span><button type="button" data-image-close aria-label="Close image preview" title="Close">${svg('close')}</button></header><img src="${escape(download?source+'&full=true':source)}" alt="${escape(name)}">${download?`<a class="chat-image-download" href="${escape(download)}" download>${svg('download')}Download original</a>`:''}`;
      dialog.showModal();
    });
    return {
      draft(file,remove=''){const source=imageFile(file)?local(file):'';return source?tile(file.name,source,remove):'';},
      sent(file){const url=downloadURL(file.download_url);return url && imageFile(file)?tile(file.name,url+'?preview=true','',url):'';},
      retain(files){const keep=new Set(files);for(const [file,url] of urls)if(!keep.has(file)){view.URL.revokeObjectURL(url);urls.delete(file);}},
    };
  }
  const api={create,imageFile,downloadURL};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  root.ChatImages=api;
})(typeof window==='undefined'?globalThis:window);
