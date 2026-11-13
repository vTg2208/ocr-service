/* Document mapping and the approved community Atlas for reviewers and admins. */
(() => {
  const $=id=>document.getElementById(id);
  let ticket=null, points=[], closed=false, busy=false, drawMap=null, drawn=null, context=null, atlas=null, atlasLayer=null;
  const status=text=>{$('staffDrawState').textContent=text;};
  function render(){
    if(!drawn)return;drawn.clearLayers();
    const latlngs=points.map(([lon,lat])=>[lat,lon]);
    if(points.length>1)(points.length>=3?L.polygon(latlngs,{color:'#236744',fillOpacity:.22,interactive:false,dashArray:closed?null:'7 5'}):L.polyline(latlngs,{color:'#236744',interactive:false})).addTo(drawn);
    points.forEach(([lon,lat],i)=>L.marker([lat,lon],{interactive:false,icon:L.divIcon({className:'',html:`<span class="corner-marker">${i+1}</span>`,iconSize:[24,24],iconAnchor:[12,12]})}).addTo(drawn));
    $('staffSaveMappedPlot').disabled=!closed||busy;$('staffCloseBoundary').disabled=points.length<3||closed||busy;
    $('staffUndoCorner').disabled=!points.length||busy;$('staffClearBoundary').disabled=busy;
    status(`${points.length} corners · ${closed?'Boundary closed. Ready to submit.':'Click corners, then close the boundary.'}`);
  }
  function selectTicket(value){
    if(busy && ticket?.id!==value.id)return;
    if(ticket?.id!==value.id){points=[];closed=false;$('staffMapForm').reset();$('staffMapForm').elements.name.value=value.subject.slice(0,120);}
    ticket=value;
    $('staffMappingSection').hidden=value.category!=='plot_mapping'||!!value.plot_id;
    if($('staffMappingSection').hidden)return;
    if(typeof L==='undefined'){status('The map did not load. Refresh when your connection is available.');return;}
    if(!drawMap){drawMap=L.map('staffDrawMap').setView([11.6643,78.146],10);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(drawMap);context=L.geoJSON(null,{style:{color:'#b77220',fillOpacity:.12,interactive:false}}).addTo(drawMap);drawn=L.featureGroup().addTo(drawMap);
      drawMap.on('click',e=>{if(busy||closed||e.originalEvent?.detail>1)return;const p=[e.latlng.lng,e.latlng.lat];if(points.length>=499){status('Use at most 499 corners.');return;}if(points.some(q=>q[0]===p[0]&&q[1]===p[1]))return;points.push(p);render();});}
    requestAnimationFrame(()=>drawMap.invalidateSize());render();
    FRAApi.request('/api/user/atlas').then(data=>{context.clearLayers();context.addData(data);}).catch(e=>status(e.message));
  }
  $('staffUndoCorner').onclick=()=>{if(busy)return;points.pop();closed=false;render();};
  $('staffClearBoundary').onclick=()=>{if(busy)return;points=[];closed=false;render();};
  $('staffCloseBoundary').onclick=()=>{if(busy||points.length<3)return;closed=true;render();};
  $('staffMapLocate').onsubmit=e=>{e.preventDefault();const f=e.currentTarget;if(drawMap)drawMap.setView([Number(f.elements.lat.value),Number(f.elements.lon.value)],18);};
  $('staffMapForm').onsubmit=async e=>{e.preventDefault();if(!ticket||!closed||busy)return;const f=e.currentTarget;busy=true;render();
    const id=ticket.id;try{const result=await FRAApi.request(`/api/staff/portal/tickets/${id}/plot`,FRAApi.json('POST',{name:f.elements.name.value.trim(),village:f.elements.village.value.trim(),coordinates:points,expected_revision:ticket.revision}));points=[];closed=false;ticket.plot_id=result.plot_id;$('staffMappingSection').hidden=true;await window.StaffPortal.mapped(result);}
    catch(error){status(error.message);$('staffStatus').textContent=error.message;$('staffStatus').dataset.error='true';if(error.status===409)await window.StaffPortal.refresh();}finally{busy=false;$('staffSaveMappedPlot').disabled=!closed;}};
  async function loadAtlas(){
    const data=await FRAApi.request('/api/user/atlas');
    if(typeof L==='undefined')throw Error('The map did not load. Refresh when your connection is available.');
    if(!atlas){atlas=L.map('staffCommunityMap').setView([11.6643,78.146],8);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(atlas);atlasLayer=L.geoJSON(null,{style:f=>({color:f.properties.color,fillOpacity:.27}),onEachFeature:(f,layer)=>{const label=document.createElement('span');label.textContent=f.properties.label;layer.bindTooltip(label,{permanent:true,direction:'center'});}}).addTo(atlas);}
    atlasLayer.clearLayers();atlasLayer.addData(data);atlas.invalidateSize();if(data.features.length)atlas.fitBounds(atlasLayer.getBounds(),{padding:[24,24],maxZoom:15});$('staffAtlasSummary').textContent=`${data.features.length} approved boundaries.`;
  }
  window.StaffPortalMaps={selectTicket,loadAtlas,canSwitchTicket:id=>id===ticket?.id||(!busy&&(!points.length||window.confirm('Discard the unfinished boundary for this request?')))};
  window.addEventListener('beforeunload',e=>{if(points.length||busy){e.preventDefault();e.returnValue='';}});
})();
