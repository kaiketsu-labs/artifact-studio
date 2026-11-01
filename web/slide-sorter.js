/* Zoom from the editing canvas into the existing, fully interactive slide rail. */
((root)=>{
  'use strict';
  const clamp=value=>Math.max(10,Math.min(400,Math.round(value/5)*5));
  /** Fit complete rows in the available canvas, keeping a bounded overview zoom. */
  function fitZoom(count,width,height,reference=700){
    for(let columns=1;columns<=Math.max(1,count);columns++){
      const card=(width-22*(columns-1))/columns;
      if(Math.ceil(count/columns)*(card*9/16+50)-22<=height)return Math.max(10,Math.min(50,Math.floor(card/reference*20)*5));
    }
    return 10;
  }
  /** Keep arrow navigation spatial in a responsive grid. */
  function nextIndex(index,key,count,columns=1){
    const offset={ArrowLeft:-1,ArrowRight:1,ArrowUp:-columns,ArrowDown:columns}[key]||0;
    return Math.max(0,Math.min(count-1,index+offset));
  }
  function create({workspace,rail,preview,canvas=null,controls,read,prepare,onChange=()=>{},edit,blocked=()=>false,requestFrame=callback=>root.requestAnimationFrame(callback),cancelFrame=id=>root.cancelAnimationFrame(id)}){
    const {editButton,gridButton,minus,plus,range,value,fit,status}=controls;
    let zoom=100,editorZoom=100,reference=700,autoFit=false,changing=false,queued=null,applying=null,wheel=0,frame=null,epoch=0;
    let renderedView=null,renderedEditorZoom=null,renderedCardWidth=null,renderedDense=null;
    const isOpen=()=>zoom<=50;
    /** Retain a slide coordinate at the pointer, or the visible centre for toolbar zoom. */
    function captureAnchor(point){
      if(!canvas)return null;
      const surface=preview.querySelector('iframe')||preview,bounds=surface.getBoundingClientRect(),viewport=canvas.getBoundingClientRect();
      const left=Math.max(bounds.left,viewport.left+canvas.clientLeft),top=Math.max(bounds.top,viewport.top+canvas.clientTop),right=Math.min(bounds.right,viewport.left+canvas.clientLeft+canvas.clientWidth),bottom=Math.min(bounds.bottom,viewport.top+canvas.clientTop+canvas.clientHeight);
      if(right<=left||bottom<=top||!bounds.width||!bounds.height)return null;
      const inside=point&&point.x>=left&&point.x<=right&&point.y>=top&&point.y<=bottom;
      const x=inside?point.x:(left+right)/2,y=inside?point.y:(top+bottom)/2;
      return {surface,x,y,u:(x-bounds.left)/bounds.width,v:(y-bounds.top)/bounds.height};
    }
    function restoreAnchor(anchor){
      if(!anchor||!anchor.surface.isConnected)return;
      const bounds=anchor.surface.getBoundingClientRect();
      canvas.scrollLeft+=bounds.left+anchor.u*bounds.width-anchor.x;
      canvas.scrollTop+=bounds.top+anchor.v*bounds.height-anchor.y;
    }
    function size(){const bounds=rail.getBoundingClientRect();return {width:Math.max(100,bounds.width-56),height:Math.max(100,bounds.height-48)};}
    function render(){
      const state=read(),overview=isOpen();
      if(renderedView!==overview){
        renderedView=overview;workspace.classList.toggle('is-sorter',overview);
        editButton.title=overview?'Return to editing':'Edit view';editButton.setAttribute('aria-pressed',String(!overview));gridButton.setAttribute('aria-pressed',String(overview));
        root.SlideIcons.button(fit,'fit',overview?'Fit all slides':'Fit slide');
      }
      if(renderedEditorZoom!==editorZoom){renderedEditorZoom=editorZoom;preview.style.setProperty('--editor-zoom',String(editorZoom/100));}
      const cardWidth=reference*zoom/100,dense=overview&&cardWidth<140;
      if(overview&&renderedCardWidth!==cardWidth){renderedCardWidth=cardWidth;rail.style.setProperty('--sorter-card-width',`${cardWidth}px`);}
      if(renderedDense!==dense){renderedDense=dense;workspace.classList.toggle('is-dense-sorter',dense);}
      range.value=String(queued?.next??applying?.next??zoom);range.setAttribute('aria-valuetext',`${zoom}% · ${overview?'Slide sorter':'Edit view'}`);value.textContent=`${zoom}%`;
      for(const control of [editButton,gridButton,minus,plus,range,fit])control.disabled=!state||blocked();
      minus.disabled||=zoom===10;plus.disabled||=zoom===400;
      status.textContent=state?(overview?`${state.count} slides`:`${state.index+1} / ${state.count}`):'';
    }
    function schedule(){if(frame===null&&!changing&&queued)frame=requestFrame(()=>void flush());}
    /** Apply only the latest pointer/wheel target per frame; never replay a stale backlog. */
    function set(next,{fitting=false,point=null}={}){
      queued?.resolve(false);
      const result=new Promise((resolve,reject)=>{queued={next:clamp(next),fitting,point,resolve,reject};});
      schedule();return result;
    }
    async function flush(){
      frame=null;if(!queued)return;
      let request=queued;queued=null;
      const generation=epoch;changing=true;applying=request;
      try{
        if(!read()||blocked()){applying=null;render();request.resolve(false);return;}
        if(request.next===zoom&&!request.fitting){request.resolve(true);return;}
        const ready=await prepare();
        if(generation!==epoch){request.resolve(false);return;}
        if(!ready){
          queued?.resolve(false);queued=null;applying=null;render();request.resolve(false);return;
        }
        if(queued){request.resolve(false);request=queued;queued=null;applying=request;}
        if(!read()||blocked()){applying=null;render();request.resolve(false);return;}
        const wasOpen=isOpen(),anchor=!wasOpen&&request.next>50&&!request.fitting?captureAnchor(request.point):null;
        if(!wasOpen&&request.next<=50){const width=preview.getBoundingClientRect().width;if(width)reference=width/(editorZoom/100);}
        applying=null;zoom=request.next;autoFit=request.fitting&&zoom<=50;
        if(!isOpen())editorZoom=zoom;
        render();
        if(autoFit){const bounds=size();zoom=fitZoom(read().count,bounds.width,bounds.height,reference);render();}
        if(wasOpen!==isOpen()){
          onChange(true);
          if(document.activeElement!==range)rail.querySelector('[aria-current="true"]')?.focus({preventScroll:true});
        }
        restoreAnchor(anchor);
        request.resolve(true);
      }catch(error){request.reject(error);}
      finally{changing=false;applying=null;schedule();}
    }
    function zoomWheel(event,sourceFrame=null){
      if(!event.ctrlKey&&!event.metaKey||event.altKey||blocked()||!read())return;
      event.preventDefault();wheel+=event.deltaY;
      if(Math.abs(wheel)>=12){
        const direction=wheel>0?-1:1;wheel=0;
        const offset=sourceFrame?.getBoundingClientRect(),point=Number.isFinite(event.clientX)&&Number.isFinite(event.clientY)?{x:event.clientX+(offset?.left||0),y:event.clientY+(offset?.top||0)}:null;
        void set((queued?.next??applying?.next??zoom)+direction*5,{point});
      }
    }
    root.SlideIcons.button(editButton,'slides','Edit view');root.SlideIcons.button(gridButton,'grid','Slide sorter');
    root.SlideIcons.button(minus,'minus','Zoom out · zoom further out for slide sorter');root.SlideIcons.button(plus,'plus','Zoom in');
    editButton.addEventListener('click',()=>void set(editorZoom));gridButton.addEventListener('click',()=>void set(50,{fitting:true}));
    minus.addEventListener('click',()=>void set((queued?.next??applying?.next??zoom)-10));plus.addEventListener('click',()=>void set((queued?.next??applying?.next??zoom)+10));
    range.addEventListener('input',()=>void set(Number(range.value)));
    fit.addEventListener('click',()=>void set(isOpen()?50:100,{fitting:isOpen()}));
    workspace.addEventListener('wheel',zoomWheel,{passive:false});
    rail.addEventListener('dblclick',event=>{const button=event.target.closest('[data-id]');if(isOpen()&&button&&!blocked())void edit(button.dataset.id);});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&isOpen()&&!event.defaultPrevented&&!document.querySelector('dialog[open],:popover-open')&&!blocked()){event.preventDefault();void set(editorZoom);}});
    root.addEventListener('resize',()=>{if(autoFit&&isOpen()){const bounds=size();zoom=fitZoom(read()?.count||1,bounds.width,bounds.height,reference);render();}else render();});
    render();
    function reset(){epoch++;queued?.resolve(false);queued=null;applying=null;if(frame!==null)cancelFrame(frame);frame=null;zoom=100;editorZoom=100;autoFit=false;render();}
    return {isOpen,zoomWheel,sync:render,edit:()=>set(editorZoom),snapshot:()=>({view_zoom:zoom,editor_zoom:editorZoom}),restore:state=>{editorZoom=clamp(Math.max(55,state?.editor_zoom||100));return set(state?.view_zoom||100);},reset};
  }
  const api={create,fitZoom,nextIndex};
  if(typeof module!=='undefined')module.exports=api;else root.SlideSorter=api;
})(typeof window==='undefined'?{}:window);
