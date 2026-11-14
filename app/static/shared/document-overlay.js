/* Review field locations on the immutable frame actually consumed by OCR. */
(() => {
  let recordId=null, dialog, version=0, data=null;
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);return n;};
  const nodes={};const ns='http://www.w3.org/2000/svg';
  document.addEventListener('fra:archive-selection',event=>{
    recordId=event.detail.id||null;
    const b=document.getElementById('documentEvidenceButton');
    if(b){b.disabled=!recordId;b.dataset.documentRecord=recordId||'';}
  });
  function setup(){
    if(dialog)return;
    dialog=el('dialog');dialog.className='document-overlay-dialog';dialog.setAttribute('aria-labelledby','documentOverlayTitle');
    const header=el('header');const title=el('h2','Document evidence');title.id='documentOverlayTitle';
    const close=el('button','Close');close.type='button';close.onclick=()=>dialog.close();header.append(title,close);
    nodes.status=el('p');nodes.status.setAttribute('role','status');
    const controls=el('div');controls.className='document-overlay-controls';
    const pageLabel=el('label','Page ');nodes.page=el('select');pageLabel.append(nodes.page);
    const fieldLabel=el('label','Field ');nodes.field=el('select');fieldLabel.append(nodes.field);
    const zoomLabel=el('label','Zoom ');nodes.zoom=el('input');nodes.zoom.type='range';nodes.zoom.min=100;nodes.zoom.max=250;nodes.zoom.value=100;zoomLabel.append(nodes.zoom);
    controls.append(pageLabel,fieldLabel,zoomLabel);
    nodes.source=el('a','Download original document');nodes.source.target='_blank';nodes.source.rel='noopener';
    nodes.text=el('p');nodes.viewport=el('div');nodes.viewport.className='document-overlay-viewport';
    nodes.svg=document.createElementNS(ns,'svg');nodes.svg.setAttribute('role','img');nodes.svg.setAttribute('aria-label','Saved OCR image with selected field location');nodes.viewport.append(nodes.svg);
    dialog.append(header,el('p','Coordinates refer to the saved, processed OCR image. Extraction confidence is not factual accuracy.'),controls,nodes.source,nodes.status,nodes.text,nodes.viewport);
    nodes.page.onchange=render;nodes.field.onchange=()=>{const f=data?.fields[nodes.field.value];if(f?.page)nodes.page.value=String(f.page);render();};
    nodes.zoom.oninput=()=>{nodes.svg.style.width=nodes.zoom.value+'%';};
    dialog.addEventListener('close',()=>{version++;nodes.svg.replaceChildren();});document.body.append(dialog);
  }
  function render(){
    nodes.svg.replaceChildren();
    const page=data?.items.find(p=>String(p.page)===nodes.page.value);
    if(!page){nodes.status.textContent='No saved OCR frame exists for this extraction. Reprocess the source to generate one.';return;}
    nodes.svg.setAttribute('viewBox',`0 0 ${page.width} ${page.height}`);
    const image=document.createElementNS(ns,'image');image.setAttribute('href',page.image_url);image.setAttribute('width',page.width);image.setAttribute('height',page.height);nodes.svg.append(image);
    const field=data.fields[nodes.field.value];nodes.text.textContent=field?.text?`Source text: ${field.text}`:'';
    nodes.status.textContent=`Saved page ${page.page}; ${page.width} × ${page.height} pixels.`;
    if(field?.box&&Number(field.page)===page.page){
      const [left,top,right,bottom]=field.box;const rect=document.createElementNS(ns,'rect');
      for(const [name,value]of Object.entries({x:Math.max(0,left),y:Math.max(0,top),width:Math.max(0,Math.min(page.width,right)-Math.max(0,left)),height:Math.max(0,Math.min(page.height,bottom)-Math.max(0,top)),fill:'none',stroke:'#c26b00','stroke-width':4}))rect.setAttribute(name,value);
      nodes.svg.append(rect);
    }else if(nodes.field.value)nodes.status.textContent+=' No bounding box was retained for this field on this page.';
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-document-record], [data-document-field]');if(!button)return;
    const id=button.dataset.documentRecord||recordId;if(!id)return;
    setup();const current=++version;data=null;nodes.svg.replaceChildren();nodes.status.textContent='Loading source evidence…';if(!dialog.open)dialog.showModal();
    try{
      const record=await FRAApi.request(`/api/fra/archive/records/${id}`);
      if(current!==version)return;
      const run=record.extraction_runs.at(-1);if(!run)throw new Error('No extraction has run for this document.');
      const result=await FRAApi.request(`/api/fra/archive/records/${id}/extractions/${run.id}/pages`);
      if(current!==version)return;data=result;
      nodes.source.href=result.original_url;nodes.page.replaceChildren();nodes.field.replaceChildren(new Option('Whole page',''));
      result.items.forEach(page=>nodes.page.add(new Option(`Page ${page.page}`,String(page.page))));
      Object.keys(result.fields).forEach(field=>nodes.field.add(new Option(field.replaceAll('_',' '),field)));
      nodes.field.value=button.dataset.documentField||'';
      const selected=result.fields[nodes.field.value];if(selected?.page)nodes.page.value=String(selected.page);render();
    }catch(error){if(current===version)nodes.status.textContent=error.message;}
  });
})();
