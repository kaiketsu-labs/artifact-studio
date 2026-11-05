/* Shared, local SVG vocabulary for review and presentation controls. */
(() => {
  const paths={
    search:'m21 21-5-5M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15',
    star:'m12 3 2.8 5.7 6.3.9-4.5 4.4 1.1 6.2-5.7-3-5.7 3 1.1-6.2-4.5-4.4 6.3-.9L12 3Z',
    share:'M12 15V3m-4 4 4-4 4 4M5 11H3v10h18V11h-2',
    mail:'M3 5h18v14H3zM3 6l9 7 9-7',
    sectionAdd:'M3 5h18M3 10h11M3 15h8M17 13v8M13 17h8',
    more:'M5 12h.01M12 12h.01M19 12h.01',
    menu:'M4 6h16M4 12h16M4 18h16',
    sort:'M4 6h16M4 12h11M4 18h6',
    panelLeft:'M3 4h18v16H3zM8 4v16',
    chat:'M21 11a8 8 0 0 1-8 8H8l-5 3V6a3 3 0 0 1 3-3h7a8 8 0 0 1 8 8ZM7 8h10M7 12h7',
    question:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM9.1 8a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4M12 17h.01',
    clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 6v6l4 2',
    history:'M3 11a9 9 0 1 1 2.6 7.4M3 4v7h7M12 7v5l3 2',
    info:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 11v6M12 7h.01',
    reply:'m9 4-6 6 6 6M3 10h10a7 7 0 0 1 7 7v3',
    user:'M20 21v-2a8 8 0 0 0-16 0v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
    robot:'M12 3v4M9 3h6M7 7h10a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3ZM2 11v5m20-5v5M9 12h.01M15 12h.01M9 17h6',
    sun:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5',
    moon:'M20.5 13a8.5 8.5 0 0 1-9.5-9.5A8.5 8.5 0 1 0 20.5 13Z',monitor:'M3 4h18v13H3zM12 17v4m-4 0h8',
    palette:'M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-3.7 1.5 1.5 0 0 1 1-2.3h2a4 4 0 0 0 4-4c0-4.4-4-8-9-8ZM7 10h.01M10 7h.01M15 7h.01M18 10h.01',
    grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    align:'M3 4v16M7 7h13v4H7zM7 15h8v3H7z',
    alignLeft:'M4 3v18M8 6h12v4H8zM8 14h8v4H8z',alignCenter:'M12 2v3m0 5v4m0 4v4M4 5h16v5H4zM7 14h10v4H7z',alignRight:'M20 3v18M4 6h12v4H4zM8 14h8v4H8z',
    alignTop:'M3 4h18M6 8h4v12H6zM14 8h4v8h-4z',alignMiddle:'M2 12h3m5 0h4m4 0h4M5 4h5v16H5zM14 7h4v10h-4z',alignBottom:'M3 20h18M6 4h4v12H6zM14 8h4v8h-4z',
    slideAdd:'M3 5h18v14H3zM12 8v8M8 12h8',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
    code:'m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18',pdf:'M5 3h9l5 5v13H5zM14 3v6h5M8 13h8M8 17h6',
    up:'m6 14 6-6 6 6',down:'m6 10 6 6 6-6',first:'M5 4h14M12 20V8m-5 5 5-5 5 5',last:'M5 20h14M12 4v12m-5-5 5 5 5-5',
    eyeOff:'m3 3 18 18M10.6 5.1A12 12 0 0 1 12 5c6 0 10 7 10 7a21 21 0 0 1-4 4M6.1 6.1C3.5 8.1 2 12 2 12s4 7 10 7a12 12 0 0 0 5.1-1.1M10 10a3 3 0 0 0 4 4',
    rules:'m3 5 2 2 3-4M11 5h10M3 12l2 2 3-4M11 12h10M3 19l2 2 3-4M11 19h10',
    edit:'m16 3 5 5-13 13H3v-5L16 3Zm-3 3 5 5',
    move:'M12 2v20M2 12h20M8 6l4-4 4 4M8 18l4 4 4-4M6 8l-4 4 4 4M18 8l4 4-4 4',
    archive:'M3 4h18v5H3zM5 9v12h14V9M9 13h6',
    restore:'M4 4v6h6M4 10a8 8 0 1 1 0 5',
    folder:'M3 6h7l2 3h9v11H3zM3 6V4h7l2 2h8v3',
    folderOpen:'M3 10V5h7l2 3h9v3M3 10h19l-3 10H4L2 10',
    folderPlus:'M3 6h7l2 3h9v11H3zM3 6V4h7l2 2h8v3M12 12v6m-3-3h6',
    project:'M4 7h16v14H4zM8 7V3h8v4M4 12h16M10 11v3h4v-3',
    github:'M9 19c-4 1-4-2-6-2m12 5v-4a3 3 0 0 0-1-2c4-.5 6-2 6-6a5 5 0 0 0-1-3 5 5 0 0 0 0-4s-2-.5-4 1a13 13 0 0 0-6 0C7 2.5 5 3 5 3a5 5 0 0 0 0 4 5 5 0 0 0-1 3c0 4 2 5.5 6 6a3 3 0 0 0-1 2v4',
    upload:'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6',
    download:'M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5',
    image:'M3 3h18v18H3zM3 16l5-5 5 5 3-3 5 5M15 7h.01',
    table:'M3 4h18v16H3zM3 9h18M3 14h18M9 4v16M15 9v11',
    font:'m4 20 7-16h2l7 16M7 14h10',
    file:'M5 3h9l5 5v13H5zM14 3v6h5',
    external:'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
    swap:'M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4',
    pause:'M8 4v16M16 4v16',play:'m7 4 13 8-13 8V4Z',stop:'M6 6h12v12H6z',
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
    fit:'M3 5h18v14H3zM7 9h10v6H7z',
    cursor:'m4 3 15 9-7 2-3 7-5-18Z',
    spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
    slides:'M3 5h18v14H3zM8 5v14M3 10h5M3 14h5'
  };
  /** Official brand marks (filled), used to identify the connected agent harness. */
  const brands={'claude-code':['0 0 35 24','M24.5475 0H19.3384L28.8374 24H34.0465L24.5475 0ZM9.49897 0L0 24H5.31125L7.25395 18.96H17.1914L19.1341 24H24.4454L14.9464 0H9.49897ZM8.97193 14.5029L12.2227 6.06857L15.4735 14.5029H8.97193Z'],codex:['118.557 119.958 484.139 479.818','M304.246 294.611V249.028C304.246 245.189 305.687 242.309 309.044 240.392L400.692 187.612C413.167 180.415 428.042 177.058 443.394 177.058C500.971 177.058 537.44 221.682 537.44 269.182C537.44 272.54 537.44 276.379 536.959 280.218L441.954 224.558C436.197 221.201 430.437 221.201 424.68 224.558L304.246 294.611ZM518.245 472.145V363.224C518.245 356.505 515.364 351.707 509.608 348.349L389.174 278.296L428.519 255.743C431.877 253.826 434.757 253.826 438.115 255.743L529.762 308.523C556.154 323.879 573.905 356.505 573.905 388.171C573.905 424.636 552.315 458.225 518.245 472.141V472.145ZM275.937 376.182L236.592 353.152C233.235 351.235 231.794 348.354 231.794 344.515V238.956C231.794 187.617 271.139 148.749 324.4 148.749C344.555 148.749 363.264 155.468 379.102 167.463L284.578 222.164C278.822 225.521 275.942 230.319 275.942 237.039V376.186L275.937 376.182ZM360.626 425.122L304.246 393.455V326.283L360.626 294.616L417.002 326.283V393.455L360.626 425.122ZM396.852 570.989C376.698 570.989 357.989 564.27 342.151 552.276L436.674 497.574C442.431 494.217 445.311 489.419 445.311 482.699V343.552L485.138 366.582C488.495 368.499 489.936 371.379 489.936 375.219V480.778C489.936 532.117 450.109 570.985 396.852 570.985V570.989ZM283.134 463.99L191.486 411.211C165.094 395.854 147.343 363.229 147.343 331.562C147.343 294.616 169.415 261.509 203.48 247.593V356.991C203.48 363.71 206.361 368.508 212.117 371.866L332.074 441.437L292.729 463.99C289.372 465.907 286.491 465.907 283.134 463.99ZM277.859 542.68C223.639 542.68 183.813 501.895 183.813 451.514C183.813 447.675 184.294 443.836 184.771 439.997L279.295 494.698C285.051 498.056 290.812 498.056 296.568 494.698L417.002 425.127V470.71C417.002 474.549 415.562 477.429 412.204 479.346L320.557 532.126C308.081 539.323 293.206 542.68 277.854 542.68H277.859ZM396.852 599.776C454.911 599.776 503.37 558.513 514.41 503.812C568.149 489.896 602.696 439.515 602.696 388.176C602.696 354.587 588.303 321.962 562.392 298.45C564.791 288.373 566.231 278.296 566.231 268.224C566.231 199.611 510.571 148.267 446.274 148.267C433.322 148.267 420.846 150.184 408.37 154.505C386.775 133.392 357.026 119.958 324.4 119.958C266.342 119.958 217.883 161.22 206.843 215.921C153.104 229.837 118.557 280.218 118.557 331.557C118.557 365.146 132.95 397.771 158.861 421.283C156.462 431.36 155.022 441.437 155.022 451.51C155.022 520.123 210.682 571.466 274.978 571.466C287.931 571.466 300.407 569.549 312.883 565.228C334.473 586.341 364.222 599.776 396.852 599.776Z']};
  const brand=harness=>brands[harness]?`<svg class="ui-icon brand-icon" viewBox="${brands[harness][0]}" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="${brands[harness][1]}"/></svg>`:'';
  const svg=name=>`<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.slides}"/></svg>`;
  const rendered=new WeakMap();
  const button=(element,name,label,showLabel=false)=>{
    if(!element)return;
    const previous=rendered.get(element);
    if(!previous||previous.name!==name||previous.label!==label||previous.showLabel!==showLabel||previous.html!==element.innerHTML){
      element.innerHTML=svg(name);
      if(showLabel){const text=document.createElement('span');text.textContent=label;element.append(text);}
      rendered.set(element,{name,label,showLabel,html:element.innerHTML});
    }
    if(element.title!==label)element.title=label;
    if(element.getAttribute('aria-label')!==label)element.setAttribute('aria-label',label);
    element.classList.add('icon-control');element.classList.toggle('icon-only',!showLabel);
  };
  window.SlideIcons={svg,button,brand};
})();
