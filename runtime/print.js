/* Wait for portable assets before opening the browser's PDF destination selector. */
(() => {
  'use strict';
  async function ready(){
    const contents=JSON.parse(document.getElementById('interactive-data')?.textContent||'[]');
    await Promise.all([...document.querySelectorAll('.print-page')].map((page,index)=>contents[index]?window.SlideStudioInteractive.mount(page,contents[index],{mode:'print',final:true,reduced_motion:true}).ready:Promise.resolve()));
    await document.fonts.ready;
    await Promise.all([...document.images].map(image=>image.decode().catch(()=>{})));
    if(!new URLSearchParams(location.search).has('preview'))window.print();
  }
  document.getElementById('print').addEventListener('click',()=>window.print());
  void ready();
})();
