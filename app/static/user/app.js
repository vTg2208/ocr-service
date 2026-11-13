const UserPlotUI = (() => {
  const area = (points) => {
    if (points.length < 3) return 0;
    const rad = (v) => v * Math.PI / 180;
    return Math.abs(points.reduce((sum, a, i) => { const b = points[(i + 1) % points.length]; return sum + rad(b[0] - a[0]) * (2 + Math.sin(rad(a[1])) + Math.sin(rad(b[1]))); }, 0)) * 6378137 ** 2 / 2;
  };
  function validate(points) {
    if (points.length < 3) return 'Mark at least three corners.';
    if (points.length > 499) return 'Use at most 499 corners.';
    if (new Set(points.map((p) => p.join(','))).size !== points.length) return 'Two corners share the same position. Move or undo a corner.';
    const cross = (a,b,c) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    const between = (a,b,c) => Math.min(a[0],b[0]) <= c[0] && c[0] <= Math.max(a[0],b[0]) && Math.min(a[1],b[1]) <= c[1] && c[1] <= Math.max(a[1],b[1]);
    const intersects = (a,b,c,d) => { const x = cross(a,b,c), y = cross(a,b,d), z = cross(c,d,a), w = cross(c,d,b); return (x*y < 0 && z*w < 0) || (x === 0 && between(a,b,c)) || (y === 0 && between(a,b,d)) || (z === 0 && between(c,d,a)) || (w === 0 && between(c,d,b)); };
    for (let i=0; i<points.length; i++) for (let j=i+1; j<points.length; j++) {
      if (j === i+1 || (i === 0 && j === points.length-1)) continue;
      if (intersects(points[i],points[(i+1)%points.length],points[j],points[(j+1)%points.length])) return 'The boundary crosses itself. Drag a corner or undo and try again.';
    }
    const size = area(points);
    if (size < 1 || size > 100000000) return 'Choose a plot between 1 m² and 100 km². Check the corner positions.';
    return '';
  }
  function feature(points, properties={}) { return { type:'Feature', properties, geometry:{type:'Polygon',coordinates:[[...points.map((p)=>[...p]), [...points[0]]]]} }; }
  return { area, validate, feature };
})();
if (typeof module !== 'undefined') module.exports = UserPlotUI;
if (typeof document !== 'undefined') (() => {
  const $ = (id) => document.getElementById(id);
  let map, layer, markers, points = [], drawing = true, closed = false, dirty = false, saved = null, busy = false, leaving = false;
  const say = (message, error=false) => { $('status').textContent = message; $('status').dataset.error = String(error); };
  function areaText(sqm) { return `${(sqm/10000).toLocaleString('en-IN',{maximumFractionDigits:3})} ha`; }
  function setStep(step) { ['stepLocate','stepDraw','stepReview'].forEach((id,i) => $(id).classList.toggle('current',i === step)); }
  function changed() { dirty = true; $('saveState').textContent = saved ? 'Unsaved changes' : 'Not saved'; $('saveState').classList.remove('saved'); $('downloadPlot').hidden = true; }
  function render() {
    if (!map) return;
    layer.clearLayers(); markers.clearLayers();
    const latlngs = points.map(([lon,lat]) => [lat,lon]);
    if (points.length > 1) (points.length >= 3 ? L.polygon(latlngs,{color:'#236744',weight:2,fillOpacity:closed ? 0.2 : 0.1,dashArray:closed?null:'7 5',interactive:false}) : L.polyline(latlngs,{color:'#236744',weight:3,dashArray:'7 5',interactive:false})).addTo(layer);
    points.forEach(([lon,lat],i) => {
      const marker = L.marker([lat,lon],{draggable:closed && dirty && !busy, icon:L.divIcon({className:'',html:`<span class="corner-marker">${i+1}</span>`,iconSize:[24,24],iconAnchor:[12,12]})}).addTo(markers);
      marker.bindTooltip(i === 0 && drawing ? 'Click to close boundary' : `Corner ${i+1}`);
      marker.on('click', () => { if (i === 0 && drawing) { if(points.length>=3) close(); else say('Add at least three corners before closing the boundary.'); } });
      marker.on('dragend', () => { const p=marker.getLatLng(); points[i]=[p.lng,p.lat]; changed(); render(); if (closed) { const error=UserPlotUI.validate(points); if(error) say(error,true); } });
    });
    [$('plotName'), $('plotVillage')].forEach((input) => { input.disabled = busy; });
    const invalid = closed ? UserPlotUI.validate(points) : '';
    const size = closed && !invalid ? UserPlotUI.area(points) : null;
    $('areaValue').textContent = size === null ? '—' : areaText(size);
    $('areaAlternate').textContent = size === null ? (invalid || 'Close the boundary to calculate') : `${size.toLocaleString('en-IN',{maximumFractionDigits:1})} m² · ${(size/4046.8564224).toFixed(3)} acres`;
    $('cornerCount').textContent = `${points.length} corners`; $('coordinateCount').textContent=`(${points.length})`;
    $('coordinateList').replaceChildren(...points.map(([lon,lat])=>{ const li=document.createElement('li'); li.textContent=`${lon.toFixed(6)}, ${lat.toFixed(6)}`; return li; }));
    $('startDrawing').hidden = closed; $('startDrawing').disabled = busy; $('startDrawing').textContent = drawing ? 'Pause marking' : 'Resume marking';
    $('closeBoundary').hidden = !drawing; $('closeBoundary').disabled=busy || points.length<3;
    $('undoPoint').disabled=busy || !points.length || !dirty; $('clearBoundary').disabled=busy || !points.length;
    $('savePlot').disabled=busy || !closed || !!invalid || !dirty;
    $('checkOverlap').disabled=busy || !closed || !!invalid;
    $('submitPlot').disabled=busy || !saved || dirty;
    document.dispatchEvent(new CustomEvent('user:plot-state')); 
    $('editBoundary').hidden=!closed || dirty; $('downloadPlot').hidden=!saved || dirty;
    $('mapHeading').textContent=closed ? 'Review your boundary' : drawing ? 'Mark the corners in order' : 'Find your land';
    $('mapHint').textContent=closed ? (dirty ? 'Drag a numbered corner to adjust the boundary.' : 'Your saved boundary is linked to your account.') : drawing ? 'Click each corner around the edge of your plot.' : 'Move and zoom the map, or enter a nearby coordinate.';
    map.getContainer().style.cursor = drawing ? 'crosshair' : '';
    setStep(closed ? 2 : drawing ? 1 : 0);
  }
  function close() { const error=UserPlotUI.validate(points); if(error) {say(error,true);return;} closed=true; drawing=false; changed(); render(); say('Boundary closed. Check the shape, give your plot a name, then save.'); }
  function discardOK() { return !dirty || window.confirm('Discard the unsaved changes to this plot?'); }
  function switchView(savedView) {
    if (window.UserPortal) window.UserPortal.navigate(location.hash || '#dashboard');
    else { $('mapSection').hidden = location.hash !== '#map'; $('savedSection').hidden = !savedView; }
    if(savedView) loadPlots(); else if(map) requestAnimationFrame(()=>map.invalidateSize());
  }
  function newPlot() {
    if(busy || !discardOK()) return;
    points=[];drawing=true;closed=false;dirty=false;saved=null; $('plotName').value='';$('plotVillage').value='';$('detailTitle').textContent='Your new plot';$('saveState').textContent='Not saved';$('saveState').classList.remove('saved'); location.hash='map';switchView(false);render();say('Find your location, then click each corner of your plot.');
  }
  function showPlot(plot) {
    if(busy || !discardOK()) return;
    saved=plot; const ring=plot.geometry.coordinates[0][0]; points=ring.slice(0,-1).map((p)=>[...p]);closed=true;drawing=false;dirty=false;
    $('plotName').value=plot.name;$('plotVillage').value=plot.village;$('detailTitle').textContent=plot.name;$('saveState').textContent=`Saved · revision ${plot.revision}`;$('saveState').classList.add('saved');
    location.hash='map';switchView(false);render();map.fitBounds(L.latLngBounds(points.map(([lon,lat])=>[lat,lon])),{padding:[40,40],maxZoom:19});say('Saved plot opened. You can download it or edit its boundary and details.');
  }
  async function loadPlots() {
    try {
      const data=await FRAApi.request('/api/user/plots');$('plotCount').textContent=data.items.length;
      const host=$('savedPlots');host.replaceChildren();
      if(!data.items.length) { const div=document.createElement('div');div.className='saved-empty';const h=document.createElement('h3');h.textContent='Your first boundary belongs here.';const p=document.createElement('p');p.textContent='Map a plot and save it to return to it anytime.';const b=document.createElement('button');b.className='primary-action';b.textContent='Map my first plot';b.onclick=newPlot;div.append(h,p,b);host.append(div); }
      data.items.forEach((plot)=>{const card=document.createElement('article');card.className='saved-card';const badge=document.createElement('span');badge.className='plot-badge saved';badge.textContent='Boundary saved';const title=document.createElement('h3');title.textContent=plot.name;const locality=document.createElement('p');locality.textContent=plot.village || 'Locality not added';const size=document.createElement('strong');size.textContent=areaText(plot.area_sqm);const updated=document.createElement('p');updated.textContent=`Updated ${new Date(plot.updated_at).toLocaleDateString('en-IN')} · revision ${plot.revision}`;const b=document.createElement('button');b.className='secondary-action';b.textContent='Open plot';b.onclick=()=>showPlot(plot);card.append(badge,title,locality,size,updated,b);host.append(card);});
    } catch(error) {say(error.message,true);}
  }
  async function save(event) {
    event.preventDefault();if(busy || !closed || UserPlotUI.validate(points) || !dirty)return;
    busy=true;render();$('savePlot').textContent='Saving…';
    const payload={name:$('plotName').value.trim(),village:$('plotVillage').value.trim(),coordinates:points,...(saved ? {expected_revision:saved.revision} : {})};
    try {const plot=await FRAApi.request(saved ? `/api/user/plots/${saved.id}` : '/api/user/plots',FRAApi.json(saved ? 'PUT' : 'POST',payload));saved=plot;dirty=false;$('detailTitle').textContent=plot.name;$('saveState').textContent=`Saved · revision ${plot.revision}`;$('saveState').classList.add('saved');say('Your plot is saved to your account. You can find it in My saved plots.');await loadPlots();document.dispatchEvent(new CustomEvent('user:plot-saved'));}
    catch(error) {if(error.status===401) location.assign('/login');else say(error.message,true);}
    finally {busy=false;$('savePlot').textContent='Save my plot';render();}
  }
  async function init() {
    try {const user=await FRAApi.request('/api/auth/session');$('userName').textContent=user.display_name || user.external_id;}
    catch(error) {if(error.status===401)location.assign('/login');else say(error.message,true);return;}
    if(typeof L==='undefined') {say('The map library could not load. Check your internet connection and reload the page.',true);return;}
    map=L.map('plotMap',{doubleClickZoom:false}).setView([11.6643,78.1460],10);
    const streets=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
    const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics and the GIS User Community'});
    L.control.layers({'Street map':streets,'Satellite basemap':satellite}).addTo(map);
    let tileErrors=0;[streets,satellite].forEach((tiles)=>tiles.on('tileerror',()=>{if(++tileErrors===3)say('Some map tiles could not load. Check your connection or switch the basemap.',true);}));
    layer=L.featureGroup().addTo(map);markers=L.featureGroup().addTo(map);
    let approvedLayer=L.geoJSON(null,{style:(f)=>({color:f.properties.color,fillOpacity:.13,weight:2}),onEachFeature:(feature,layer)=>{const label=document.createElement('span');label.textContent=feature.properties.label;layer.bindTooltip(label,{permanent:true,direction:'center'});}}).addTo(map);
    document.addEventListener('user:atlas-data',(event)=>{approvedLayer.clearLayers();approvedLayer.addData(event.detail);});
    map.on('click',(event)=>{if(!drawing || busy)return;if(event.originalEvent?.detail>1)return;if(points.length && points.at(-1)[0]===event.latlng.lng && points.at(-1)[1]===event.latlng.lat)return;if(points.length>=499){say('Use at most 499 corners.',true);return;}points.push([event.latlng.lng,event.latlng.lat]);changed();render();});
    $('startDrawing').onclick=()=>{drawing=!drawing;render();say(drawing?'Click each corner in order. The polygon previews automatically after three points.':'Marking paused. Move the map, then resume when ready.');};
    $('undoPoint').onclick=()=>{if(busy)return;points.pop();closed=false;drawing=true;changed();render();say('Last corner removed. Continue marking or close the boundary.');};
    $('closeBoundary').onclick=close;
    $('clearBoundary').onclick=()=>{if(busy || !confirm('Clear this boundary? The last saved version remains until you save again.'))return;points=[];closed=false;drawing=true;changed();render();};
    $('editBoundary').onclick=()=>{changed();render();say('Drag the numbered corners to adjust the shape. Use Undo corner to continue marking.');};
    $('newPlot').onclick=newPlot;
    $('coordinateSearch').onsubmit=(event)=>{event.preventDefault();map.setView([Number($('latitude').value),Number($('longitude').value)],18);say('Location found. Zoom in to check the plot edges before marking.');};
    $('locateMe').onclick=()=>{if(!navigator.geolocation){say('Location is unavailable. Enter coordinates or move the map instead.',true);return;}const b=$('locateMe');b.disabled=true;b.textContent='Locating…';navigator.geolocation.getCurrentPosition((p)=>{map.setView([p.coords.latitude,p.coords.longitude],18);say(`Approximate location found (±${Math.round(p.coords.accuracy)} m). Check the map before drawing.`);b.disabled=false;b.textContent='Use my location';},()=>{say('Could not get your location. Allow location access, enter coordinates, or move the map.',true);b.disabled=false;b.textContent='Use my location';},{timeout:12000});};
    [$('plotName'),$('plotVillage')].forEach((input)=>input.addEventListener('input',()=>{changed();render();}));
    $('savePlotForm').onsubmit=save;$('refreshPlots').onclick=loadPlots;
    $('downloadPlot').onclick=()=>{if(!saved || dirty)return;const feature=UserPlotUI.feature(points,{plot_id:saved.id,name:saved.name,revision:saved.revision,area_sqm:saved.area_sqm,source:'user_drawn',legal_status:'personal_reference'});const url=URL.createObjectURL(new Blob([JSON.stringify(feature,null,2)],{type:'application/geo+json'}));const link=document.createElement('a');link.href=url;link.download=`plot-${saved.id}.geojson`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    window.addEventListener('beforeunload',(event)=>{if(dirty && !leaving){event.preventDefault();event.returnValue='';}});
    await loadPlots();switchView(location.hash==='#saved');render();
  }
  window.UserPlotMap={ canLeave:()=>{if(busy || !discardOK())return false;leaving=true;return true;}, cancelLeave:()=>{leaving=false;}, getDraft:()=>({points,closed,dirty,saved}), refresh:loadPlots, open:showPlot, newPlot, invalidate:()=>map?.invalidateSize(), showOverlaps:(features)=>{if(!map)return; L.geoJSON(features,{style:{color:'#ad3f31',weight:3,fillOpacity:.4}}).addTo(layer);} };
  init();
})();
