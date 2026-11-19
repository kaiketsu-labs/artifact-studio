/* Studio-only playback of an immutable alternative. Offline exports use the saved deck. */

/** Keep the frozen theme and embedded assets isolated from all other slides. */
function alternativeFrame({stage,theme,root},live){
  const frame=document.createElement('iframe');
  frame.setAttribute('sandbox','allow-same-origin');frame.tabIndex=-1;frame.title='Alternative slide';
  frame.style='position:absolute;inset:0;width:100%;height:100%;border:0';
  if(!live){frame.setAttribute('aria-hidden','true');frame.style.pointerEvents='none';}
  const policy="default-src 'none'; img-src data: blob:; font-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";
  frame.srcdoc=`<!doctype html>${root}<head><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="${policy}">${theme}
    <style>
      html,body{margin:0;width:1600px;height:900px;overflow:hidden}
      #capture-stage{position:relative;width:1600px;height:900px;container-type:inline-size;box-shadow:none}
      #capture-stage>.slide{position:relative;width:100%;height:100%;box-sizing:border-box;overflow:hidden}
    </style><style id="pause-alternative">
      .slide,.slide *,.slide::before,.slide::after,.slide *::before,.slide *::after{animation-play-state:paused!important}
    </style></head><body>${stage}</body></html>`;
  return frame;
}

/** Reuse the saved stage, styles and interactive data without running capture scripts. */
export async function readAlternative(response,slideId,buildCount){
  if(!response.ok)throw new Error('Alternative unavailable.');
  const source=await response.text(),template=document.createElement('template');
  template.innerHTML=source;
  const stage=template.content.querySelector('main#capture-stage'),theme=template.content.querySelector('style');
  const data=template.content.querySelector('script#interactive-data[type="application/json"]');
  if(!stage?.querySelector('.slide')||!theme||!data)throw new Error('Invalid alternative preview.');
  const interactive=JSON.parse(data.textContent);
  if(typeof interactive!=='string')throw new Error('Invalid interactive content.');
  const root=source.match(/^<!doctype html>(<html\b[^>]*>)/i)?.[1]||'<html>';
  const snapshot={stage:stage.outerHTML,theme:theme.outerHTML,root};
  return {slideId,interactive,steps:interactive?0:buildCount(stage.innerHTML),frame:live=>alternativeFrame(snapshot,live)};
}
