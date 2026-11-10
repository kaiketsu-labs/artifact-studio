/* Wait for portable assets before opening the browser's PDF destination selector. */
(() => {
  'use strict';
  const button=document.getElementById('print'),includeHidden=document.getElementById('print-include-hidden');
  function filter(){
    const pages=[...document.querySelectorAll('.print-page')];
    for(const page of pages)page.hidden=!includeHidden.checked&&page.dataset.hidden==='true';
    const empty=pages.every(page=>page.hidden);
    document.getElementById('print-empty').hidden=!empty;
    button.disabled=empty||document.documentElement.dataset.printReady!=='true';
  }
  async function ready(){
    const contents=JSON.parse(document.getElementById('interactive-data')?.textContent||'[]');
    await Promise.all([...document.querySelectorAll('.print-page')].map((page,index)=>contents[index]?window.ArtifactStudioInteractive.mount(page,contents[index],{mode:'print',final:true,reduced_motion:true}).ready:Promise.resolve()));
    await document.fonts.ready;
    await Promise.all([...document.images].map(image=>image.decode().catch(()=>{})));
    document.documentElement.dataset.printReady='true';
    filter();
    if(!button.disabled&&!new URLSearchParams(location.search).has('preview'))window.print();
  }
  includeHidden.addEventListener('change',filter);
  button.addEventListener('click',()=>window.print());
  void ready();
})();
