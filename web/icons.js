/* Shared, local SVG vocabulary for review and presentation controls. */
(() => {
  const paths={
    rules:'m3 5 2 2 3-4M11 5h10M3 12l2 2 3-4M11 12h10M3 19l2 2 3-4M11 19h10',
    edit:'m16 3 5 5-13 13H3v-5L16 3Zm-3 3 5 5',
    move:'M12 2v20M2 12h20M8 6l4-4 4 4M8 18l4 4 4-4M6 8l-4 4 4 4M18 8l4 4-4 4',
    archive:'M3 4h18v5H3zM5 9v12h14V9M9 13h6',
    restore:'M4 4v6h6M4 10a8 8 0 1 1 0 5',
    folder:'M3 6h7l2 3h9v11H3zM3 6V4h7l2 2h8v3',
    github:'M9 19c-4 1-4-2-6-2m12 5v-4a3 3 0 0 0-1-2c4-.5 6-2 6-6a5 5 0 0 0-1-3 5 5 0 0 0 0-4s-2-.5-4 1a13 13 0 0 0-6 0C7 2.5 5 3 5 3a5 5 0 0 0 0 4 5 5 0 0 0-1 3c0 4 2 5.5 6 6a3 3 0 0 0-1 2v4',
    upload:'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6',
    external:'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
    swap:'M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4',
    pause:'M8 4v16M16 4v16',play:'m7 4 13 8-13 8V4Z',
    eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm7 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0',
    minus:'M5 12h14',
    plus:'M12 5v14M5 12h14',
    minus:'M5 12h14',pause:'M8 5v14M16 5v14',play:'m7 4 14 8-14 8V4Z',swap:'M4 7h15l-4-4m5 14H5l4 4',eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
    attach:'m8 12 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l8-8M6 14l8-8',
    plug:'M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8Zm6 9v4',
    present:'M4 3h16v12H4zM12 15v6m-5 0 5-6 5 6M8 7l5 3-5 3z',
    check:'m5 12 4 4L19 6',
    left:'m14 6-6 6 6 6',right:'m10 6 6 6-6 6',
    close:'m6 6 12 12M6 18 18 6',
    save:'M5 3h12l4 4v14H3V3h2Zm2 0v6h10V3M7 21v-8h10v8',
    notes:'M5 3h14v18H5zM8 7h8M8 11h8M8 15h5',
    sources:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
    copy:'M8 8h13v13H8zM16 8V3H3v13h5',
    send:'m3 3 18 9-18 9 4-9-4-9Zm4 9h14',
    live:'m13 2-9 12h7l-1 8 10-13h-8l1-7',
    batch:'m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
    group:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5M8 8h8v8H8z',
    refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1',
    fullscreen:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5',
    cursor:'m4 3 15 9-7 2-3 7-5-18Z',
    spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
    slides:'M3 5h18v14H3zM8 5v14M3 10h5M3 14h5'
  };
  const svg=name=>`<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.slides}"/></svg>`;
  const button=(element,name,label,showLabel=false)=>{
    if(!element)return;
    element.innerHTML=svg(name);
    if(showLabel){const text=document.createElement('span');text.textContent=label;element.append(text);}
    element.title=label;element.setAttribute('aria-label',label);
    element.classList.add('icon-control');element.classList.toggle('icon-only',!showLabel);
  };
  window.SlideIcons={svg,button};
})();
