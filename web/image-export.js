/* Export the displayed DOM without sending feedback or persisting provisional edits. */
((root)=>{
  'use strict';
  const limit=5*1024*1024;
  function dataURL(blob,view){return new Promise((resolve,reject)=>{const reader=new view.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('The image could not be read.'));reader.readAsDataURL(blob);});}
  /** Freeze a local preview, retaining painted canvas pixels and current form values. */
  async function snapshot(doc,{slide=false,selectors=[]}={}){
    const view=doc.defaultView,source=slide?doc.querySelector('.slide'):doc.documentElement;
    if(!source)throw Error('The preview is no longer available.');
    const clone=source.cloneNode(true),originals=[source,...source.querySelectorAll('*')],copies=[clone,...clone.querySelectorAll('*')];
    const nonce=view.crypto.randomUUID(),anchors=selectors.map((selector,index)=>{
      const element=doc.querySelector(selector),position=originals.indexOf(element);if(position<0)throw Error('The selection changed. Select the objects again.');
      copies[position].setAttribute('data-studio-export',`${nonce}-${index}`);return `[data-studio-export="${nonce}-${index}"]`;
    });
    for(let index=0;index<originals.length;index++){
      const node=originals[index],copy=copies[index];
      for(const attribute of [...copy.attributes])if(/^on/i.test(attribute.name))copy.removeAttribute(attribute.name);
      if(node.localName==='canvas'){
        const image=doc.createElement('img');image.src=node.toDataURL('image/png');
        for(const attribute of [...copy.attributes])image.setAttribute(attribute.name,attribute.value);
        const computed=view.getComputedStyle(node);for(const name of computed)image.style.setProperty(name,computed.getPropertyValue(name),'important');
        image.src=node.toDataURL('image/png');copy.replaceWith(image);
      }else if(node.localName==='img'&&node.currentSrc?.startsWith('blob:')){
        copy.src=await dataURL(await (await view.fetch(node.currentSrc)).blob(),view);copy.removeAttribute('srcset');
      }else if(node.localName==='input'){copy.setAttribute('value',node.value);copy.toggleAttribute('checked',node.checked);}
      else if(node.localName==='textarea')copy.textContent=node.value;
      else if(node.localName==='option')copy.toggleAttribute('selected',node.selected);
      if(node.getAnimations?.().length){const computed=view.getComputedStyle(node);for(const name of ['transform','opacity','filter','clip-path','width','height','top','left'])copy.style.setProperty(name,computed.getPropertyValue(name),'important');}
      copy.style?.setProperty('animation','none','important');copy.style?.setProperty('transition','none','important');
    }
    for(const node of clone.querySelectorAll('script,iframe,object,embed,[data-review-chrome],[data-artifact-bridge]'))node.remove();
    const html=(slide?'':'<!doctype html>')+clone.outerHTML;
    if(html.length>limit)throw Error('This preview is too large to export as an image.');
    return {preview_html:html,width:doc.documentElement.clientWidth,height:view.innerHeight,color_scheme:view.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light',...(selectors.length?{selectors:anchors}:{})};
  }
  /** Download one explicit export, then promptly release its temporary browser URL. */
  function download(file,filename){const url=root.URL.createObjectURL(file),link=root.document.createElement('a');link.href=url;link.download=filename;root.document.body.append(link);link.click();link.remove();root.setTimeout(()=>root.URL.revokeObjectURL(url),1000);}
  async function raster(image){
    await image.decode();if(!image.naturalWidth||image.naturalWidth*image.naturalHeight>32000000)throw Error('This image exceeds the 32-million-pixel export limit. Save its original file instead.');
    const canvas=root.document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);
    const file=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!file)throw Error('This image could not be exported.');download(file,'image.png');
  }
  async function save(endpoint,body){
    const response=await root.fetch(root.ArtifactSession?.path?.(endpoint) ?? endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!response.ok){let result;try{result=await response.json();}catch{}throw Error(typeof result?.detail==='string'?result.detail:'The image could not be exported.');}
    download(await response.blob(),/filename="([^"]+)"/.exec(response.headers.get('content-disposition')||'')?.[1]||'selection.png');
  }
  const api={snapshot,save,raster,download};if(typeof module!=='undefined')module.exports=api;else root.ImageExport=api;
})(typeof window==='undefined'?{}:window);
