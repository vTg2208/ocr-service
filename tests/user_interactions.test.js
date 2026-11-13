const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 const elements=new Map();class Element{constructor(){this.children=[];this.value='';this.style={};this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.classList={add(){},remove(){},toggle(){}};this.elements={namedItem(){return null}};}addEventListener(type,fn){this.listeners[type]=fn;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(){}removeAttribute(){}querySelector(){return new Element();}focus(){}reset(){}scrollIntoView(){} }
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};const docEvents={};const requests=[];const layers=[];
 const document={getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[],addEventListener:(type,fn)=>{docEvents[type]=fn;},dispatchEvent:(event)=>docEvents[event.type]?.(event),hidden:false};
 const map={handlers:{},on(type,fn){this.handlers[type]=fn;return this;},setView(){return this;},invalidateSize(){},fitBounds(){},getContainer:()=>get('plotMap')};
 const shape=(type)=>({type,handlers:{},items:[],addTo(group){group?.items?.push(this);return this;},clearLayers(){this.items=[];},addData(){},on(type,fn){this.handlers[type]=fn;return this;},bindTooltip(){return this;}});
 const L={map:()=>map,featureGroup:()=>{const g=shape('group');layers.push(g);return g;},tileLayer:()=>shape('tile'),control:{layers:()=>shape('control')},geoJSON:()=>shape('geojson'),polygon:()=>shape('polygon'),polyline:()=>shape('line'),marker:()=>shape('marker'),divIcon:x=>x,latLngBounds:x=>x};
 const location={hash:'#map',assign(){}};const window={location,addEventListener(){},confirm:()=>true};const profile={display_name:'Alice',completion_percent:100,missing_steps:[],email:'alice@gmail.com',phone:'',revision:1};
 const FRAApi={json:(method,body)=>({method,body}),request:async(url,options)=>{requests.push({url,options});if(url==='/api/auth/session')return{display_name:'Alice',role:'user'};if(url==='/api/user/plots'&&options?.method==='POST'){const p=options.body;return{id:'plot-one',name:p.name,village:p.village,revision:1,area_sqm:12000,created_at:new Date().toISOString(),updated_at:new Date().toISOString(),geometry:{type:'MultiPolygon',coordinates:[[[...p.coordinates,p.coordinates[0]]]]}};}if(url==='/api/user/dashboard')return{profile,submissions:[],counts:{plots:1},announcements:[],schemes:[],resources:[],tickets:[]};if(url==='/api/user/profile')return profile;if(url==='/api/user/atlas')return{type:'FeatureCollection',features:[]};return{items:[]};}};
 const context=vm.createContext({document,window,location,L,FRAApi,fetch:async()=>({ok:true}),navigator:{},CustomEvent:class{constructor(type,args={}){this.type=type;this.detail=args.detail;}},requestAnimationFrame:fn=>fn(),setInterval(){},setTimeout:fn=>fn(),confirm:()=>true,URL,Blob,console});
 return{context,get,map,layers,requests,document,window,profile};
}
test('new plot accepts successive clicks, previews a polygon, closes, and saves coordinates',async()=>{
 const h=harness();vm.runInContext(fs.readFileSync('app/static/user/app.js','utf8'),h.context);await tick();
 const points=[[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]];
 for(const [lng,lat]of points)h.map.handlers.click({latlng:{lng,lat}});
 assert.equal(h.window.UserPlotMap.getDraft().points.length,4);assert(h.layers[0].items.some(x=>x.type==='polygon'));
 h.get('closeBoundary').onclick();assert.equal(h.window.UserPlotMap.getDraft().closed,true);h.get('plotName').value='Family field';h.get('plotVillage').value='Salem';
 await h.get('savePlotForm').onsubmit({preventDefault(){}});
 const request=h.requests.find(r=>r.url==='/api/user/plots'&&r.options?.method==='POST');assert(request);assert.equal(request.options.body.coordinates.length,4);assert.equal(h.window.UserPlotMap.getDraft().saved.id,'plot-one');
});
test('dashboard completion component is hidden at 100 percent and returns below 100',async()=>{
 const h=harness();h.context.location.hash='#dashboard';vm.runInContext(fs.readFileSync('app/static/user/portal.js','utf8'),h.context);await tick();assert.equal(h.get('profileCompletionPrompt').hidden,true);
 h.profile.completion_percent=75;h.profile.missing_steps=['mapped_plot'];h.window.UserPortal.navigate('#dashboard');await tick();assert.equal(h.get('profileCompletionPrompt').hidden,false);
});
test('shared logout works without Leaflet or a page-specific map module',async()=>{
 const h=harness();let destination=null;h.window.location.assign=url=>destination=url;const button=h.get('logout');h.document.querySelectorAll=()=>[button];vm.runInContext(fs.readFileSync('app/static/shared/session.js','utf8'),h.context);await button.listeners.click();assert.equal(destination,'/login');
});
test('every protected workspace includes the independent logout script and button',()=>{
 for(const name of ['user','fra','staff-services','cadastral-evidence']){const html=fs.readFileSync(`app/static/${name}/index.html`,'utf8');assert.match(html,/data-logout/);assert.match(html,/\/static\/shared\/session.js/);}
});
test('staff can draw a document requester boundary and submit the request revision',async()=>{
 const h=harness();h.get('staffMapForm').elements={name:{value:''},village:{value:'Salem'}};
 let mapped=null;h.window.StaffPortal={mapped:async value=>mapped=value};
 const previous=h.context.FRAApi.request;h.context.FRAApi.request=async(url,options)=>{if(url==='/api/staff/portal/tickets/request-1/plot'){h.requests.push({url,options});return{plot_id:'created-plot',status:'submitted'};}return previous(url,options);};
 vm.runInContext(fs.readFileSync('app/static/staff-services/maps.js','utf8'),h.context);
 h.window.StaffPortalMaps.selectTicket({id:'request-1',category:'plot_mapping',subject:'Document plot',revision:3});await tick();
 for(const [lng,lat]of [[78.14,11.66],[78.141,11.66],[78.141,11.661],[78.14,11.661]])h.map.handlers.click({latlng:{lng,lat}});
 assert.equal(h.get('staffSaveMappedPlot').disabled,true);h.get('staffCloseBoundary').onclick();assert.equal(h.get('staffSaveMappedPlot').disabled,false);
 await h.get('staffMapForm').onsubmit({preventDefault(){},currentTarget:h.get('staffMapForm')});
 const request=h.requests.find(r=>r.url.endsWith('/request-1/plot'));assert.equal(request.options.body.coordinates.length,4);assert.equal(request.options.body.expected_revision,3);assert.equal(mapped.plot_id,'created-plot');assert.equal(h.get('staffMappingSection').hidden,true);
});
test('staff polling preserves in-progress drawing for the same request',async()=>{
 const h=harness();h.get('staffMapForm').elements={name:{value:''},village:{value:''}};
 vm.runInContext(fs.readFileSync('app/static/staff-services/maps.js','utf8'),h.context);const ticket={id:'request-1',category:'plot_mapping',subject:'Document plot',revision:2};h.window.StaffPortalMaps.selectTicket(ticket);
 h.map.handlers.click({latlng:{lng:78.14,lat:11.66}});h.window.StaffPortalMaps.selectTicket({...ticket,revision:3});assert.match(h.get('staffDrawState').textContent,/1 corners/);
});
test('user timeline renders persisted approval and leaves analysis pending',async()=>{
 const h=harness();h.context.location.hash='#saved';const previous=h.context.FRAApi.request;
 h.context.FRAApi.request=async(url,options)=>{const data=await previous(url,options);if(url==='/api/user/dashboard')data.plot_workflows=[{plot_id:'p1',name:'Approved plot',plot_revision:1,status:'approved',atlas_visible:true,approved_revision:1,analysis_note:'Runner not connected',steps:[{label:'Mapped',state:'done'},{label:'Awaiting review',state:'done'},{label:'Approved',state:'done'},{label:'Analysis pending',state:'pending'}]}];return data;};
 vm.runInContext(fs.readFileSync('app/static/user/portal.js','utf8'),h.context);await tick();
 const article=h.get('plotWorkflowList').children[0];const timeline=article.children.find(n=>n.className==='plot-timeline');assert.equal(timeline.children.length,4);assert.equal(timeline.children[3].className,'pending');assert.equal(h.get('profilePlotStatus').children.length,1);
});
