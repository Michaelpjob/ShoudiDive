'use strict';
var LAND_URL='/data/land.geojson';
var BAND={Minimal:'#64748b',Low:'#0ea5e9',Moderate:'#eab308',High:'#f97316',Extreme:'#dc2626'};
var LABEL={live:'Live',storm:'Storm 3d ago',swell:'+2m Swell',warm:'+4°C Warm'};
var toR=function(x){return x*Math.PI/180;},toD=function(x){return x*180/Math.PI;};
function nm(a,b){var R=6371,dp=toR(b[0]-a[0]),dl=toR(b[1]-a[1]);
 var h=Math.sin(dp/2)*Math.sin(dp/2)+Math.cos(toR(a[0]))*Math.cos(toR(b[0]))*Math.sin(dl/2)*Math.sin(dl/2);
 return 2*R*Math.asin(Math.min(1,Math.sqrt(h)))/1.852;}
function brg(a,b){var y=Math.sin(toR(b[1]-a[1]))*Math.cos(toR(b[0]));
 var x=Math.cos(toR(a[0]))*Math.sin(toR(b[0]))-Math.sin(toR(a[0]))*Math.cos(toR(b[0]))*Math.cos(toR(b[1]-a[1]));
 return (toD(Math.atan2(y,x))+360)%360;}
var CMP=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function comp(b){return CMP[Math.floor((b+11.25)/22.5)%16];}
function _ddm(v,pos,neg){var h=v>=0?pos:neg;v=Math.abs(v);var d=Math.floor(v);return d+'°'+((v-d)*60).toFixed(3)+"' "+h;}
function _fmt(ll){return {dd:ll.lat.toFixed(5)+', '+ll.lng.toFixed(5),dm:_ddm(ll.lat,'N','S')+'  '+_ddm(ll.lng,'E','W')};}

var D,F,LCH,map,coneLayer,hdrLayer,launchMarker,measLine,measHandle,wpMarker,coordbox,repLayer,spotLayer,bankLayer,portLayer;

var SPECIES=['yellowtail','dorado','bluefin','yellowfin','white seabass','bonito','barracuda','calico bass','paddy'];
var REPORTS=[];
function _cap(s){return s?(s.charAt(0).toUpperCase()+s.slice(1)):s;}
function _dev(){try{var d=localStorage.getItem('sd:paddies:dev');if(!d){d=(window.crypto&&crypto.randomUUID)?crypto.randomUUID():(String(Date.now())+Math.random().toString(16).slice(2));localStorage.setItem('sd:paddies:dev',d);}return d;}catch(e){return 'anon';}}
function _mine(){try{return JSON.parse(localStorage.getItem('sd:paddies:mine')||'[]');}catch(e){return [];}}
function _saveMine(a){try{localStorage.setItem('sd:paddies:mine',JSON.stringify(a));}catch(e){}}
function _today(){return new Date().toISOString().slice(0,10);}
function _snap(v){return Math.round(v/0.02)*0.02;}
function toast(msg){var t=document.getElementById('toast');if(!t){t=document.createElement('div');t.id='toast';t.className='toast';document.body.appendChild(t);}
 t.textContent=msg;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(function(){t.style.display='none';},4000);}
function renderReports(){if(!repLayer)return;repLayer.clearLayers();
 var nowMs=Date.now(),REPMAXAGE=7;  // days a catch stays on the map (matches server MAX_AGE_DAYS)
 REPORTS.forEach(function(r){
  // Age in days since the catch (r.date). Kelp paddies drift, so reports age
  // out: skip anything past the window (the server caps the feed too — this
  // also covers any cached/in-flight older point), then fade + shrink the rest.
  var ageD=r.date?Math.max(0,(nowMs-Date.parse(r.date+'T12:00:00Z'))/864e5):0;
  if(ageD>REPMAXAGE)return;
  var sp=(r.species&&r.species.toLowerCase()!=='paddy')?_cap(r.species):'Reported paddy';
  var conf=r.confidence||'unconfirmed',n=r.sources||1,st,lab;
  if(conf==='unconfirmed'){st={radius:5,weight:1,color:'#fca5a5',fillColor:'#ef4444',fillOpacity:.22,dashArray:'2 3'};lab=sp+' · 1 report · unconfirmed';}
  else if(conf==='strong'){st={radius:8,weight:2.5,color:'#fff',fillColor:'#ef4444',fillOpacity:1};lab=sp+' · confirmed · '+n+' anglers';}
  else{st={radius:6,weight:1.5,color:'#fff',fillColor:'#ef4444',fillOpacity:.92};lab=sp+' · confirmed · '+n+' anglers';}
  // Full strength for ~2 days, then fade + shrink toward the 7-day edge so the
  // freshest catches read loudest.
  var fade=ageD<=2?1:Math.max(.12,1-(ageD-2)/4*.88);
  st.fillOpacity=Math.round(st.fillOpacity*fade*100)/100;
  st.radius=Math.max(3,+(st.radius*(ageD<=2?1:Math.max(.6,1-(ageD-2)/4*.4))).toFixed(1));
  lab+=' · '+(ageD<1?'today':(ageD<2?'1d ago':Math.round(ageD)+'d ago'));
  L.circleMarker([r.lat,r.lng],st).bindTooltip(lab,{direction:'top',offset:[0,-4],className:'rep-tip'}).addTo(repLayer);});
 _mine().forEach(function(r){var lab=(r.species&&r.species.toLowerCase()!=='paddy')?_cap(r.species):'Paddy';
  L.circleMarker([r.lat,r.lng],{radius:6,weight:1.5,color:'#fbbf24',fillColor:'#f59e0b',fillOpacity:.45})
   .bindTooltip(lab+' · pending review',{direction:'top',offset:[0,-4],className:'rep-tip'}).addTo(repLayer);});}
function fetchReports(){fetch('/api/paddies/reports',{cache:'no-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  if(!j||!j.reports)return;REPORTS=j.reports;var ap={};REPORTS.forEach(function(r){ap[r.id]=1;});
  _saveMine(_mine().filter(function(m){return !ap[m.id]&&(Date.now()-(m.ts||0))<6048e5;}));
  renderReports();}).catch(function(){});}
function _reporter(){try{return JSON.parse(localStorage.getItem('sd:paddies:reporter')||'{}');}catch(e){return {};}}
function logCatch(){if(!wpMarker){toast('Tap the map where you caught fish, then Log a catch.');return;}
 var ll=wpMarker.getLatLng();var box=document.getElementById('picker');
 if(!box){box=document.createElement('div');box.id='picker';box.className='picker';document.body.appendChild(box);}
 var who=_reporter();var v=function(s){return (s||'').replace(/"/g,'&quot;');};
 var opts=SPECIES.map(function(s){return '<option value="'+s+'">'+(s==='paddy'?'Paddy (no fish)':_cap(s))+'</option>';}).join('');
 box.innerHTML='<div class=ph>Log a catch</div><div class=pr>at <b>'+ll.lat.toFixed(3)+', '+ll.lng.toFixed(3)+'</b></div>'
  +'<label class=pl>Email <span class=req>*</span></label><input id=pemail type=email autocomplete=email placeholder="you@example.com" value="'+v(who.email)+'">'
  +'<label class=pl>Name</label><input id=pname maxlength=60 placeholder="optional" value="'+v(who.name)+'">'
  +'<label class=pl>Species</label><select id=spsel>'+opts+'</select>'
  +'<label class=pl>Notes</label><input id=pnotes maxlength=280 placeholder="size, conditions… (optional)">'
  +'<div class=pb><button id=psub class=sub>Submit</button><button id=pcan class=can>Cancel</button></div>'
  +'<div class=pn>Snapped to ~1 nm. Email stays private (moderator only); your pin shows after review.</div>';
 box.style.display='block';
 document.getElementById('pcan').onclick=function(){box.style.display='none';};
 document.getElementById('psub').onclick=function(){
  var email=(document.getElementById('pemail').value||'').trim();
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){toast('Please enter a valid email.');return;}
  var name=(document.getElementById('pname').value||'').trim();
  var notes=(document.getElementById('pnotes').value||'').trim();
  try{localStorage.setItem('sd:paddies:reporter',JSON.stringify({email:email,name:name}));}catch(e){}
  box.style.display='none';
  submitReport(ll,document.getElementById('spsel').value,email,name,notes);};}
function submitReport(ll,species,email,name,notes){
 var body=JSON.stringify({lat:ll.lat,lng:ll.lng,species:species,date:_today(),deviceId:_dev(),email:email,name:name,notes:notes});
 fetch('/api/paddies/report',{method:'POST',headers:{'content-type':'application/json'},body:body})
  .then(function(r){return r.json().then(function(j){return {s:r.status,j:j};},function(){return {s:r.status,j:null};});})
  .then(function(o){
   if(o.j&&o.j.ok){var mine=_mine();mine.push({id:o.j.id,lat:_snap(ll.lat),lng:_snap(ll.lng),species:species,ts:Date.now()});_saveMine(mine);
    renderReports();toast('Thanks — your report is pending review.');}
   else if(o.s===429)toast('Easy — too many reports for now. Try again later.');
   else if(o.s===422)toast((o.j&&o.j.error==='valid email required')?'Please enter a valid email.':'That spot or species looks off — not logged.');
   else toast('Could not log that — please try again.');
  }).catch(function(){toast('Network error — not logged.');});}

function setReadout(ll){var f=_fmt(ll);coordbox.innerHTML='<b>'+f.dd+'</b><br/>'+f.dm
 +'<div class="hint">click map to drop a GPS waypoint</div>';}
function openWp(ll){var f=_fmt(ll);
 wpMarker.setPopupContent('<div class="wpc"><b>'+f.dd+'</b><br/>'+f.dm+'</div>'
  +'<button class="copybtn" data-c="'+f.dd+'">Copy GPS</button>');
 wpMarker.openPopup();}
function dropWp(ll){
 if(!wpMarker){wpMarker=L.marker(ll,{draggable:true,zIndexOffset:1100,icon:L.divIcon({className:'',
  html:'<div class=wp>⌖</div>',iconSize:[24,24],iconAnchor:[12,12]})}).addTo(map);
  wpMarker.bindPopup('');wpMarker.on('drag',function(){openWp(wpMarker.getLatLng());});}
 else wpMarker.setLatLng(ll);
 openWp(ll);}
function wireCopy(e){var el=e.popup.getElement();if(!el)return;var b=el.querySelector('.copybtn');
 if(!b||b._wired)return;b._wired=1;b.addEventListener('click',function(){var t=b.getAttribute('data-c');
  function done(ok){b.textContent=ok?'Copied!':t;}
  function legacy(){try{var a=document.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity=0;
   document.body.appendChild(a);a.select();var ok=document.execCommand('copy');document.body.removeChild(a);done(ok);}catch(_){done(false);}}
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(function(){done(true);},legacy);else legacy();});}

function drawBase(){coneLayer.clearLayers();
 L.geoJSON({type:'FeatureCollection',features:D.frames[F].cones},{interactive:false,style:function(f){return {color:'#f59e0b',weight:1,
  opacity:.2+.3*f.properties.opacity,fillColor:'#f59e0b',fillOpacity:.03+.14*f.properties.opacity};}}).addTo(coneLayer);}
function drawHDR(){hdrLayer.clearLayers();var h=D.frames[F].hdr;if(!h||!h.regions)return;
 h.regions.slice().sort(function(a,b){return b.level-a.level;}).forEach(function(r){
  var core=r.level<=0.35,mid=r.level>0.35&&r.level<=0.6;
  var st=core?{color:'#86efac',weight:2,fillColor:'#22c55e',fillOpacity:.55}
         :mid?{color:'#4ade80',weight:1.5,fillColor:'#16a34a',fillOpacity:.22}
         :{color:'#4ade80',weight:1,fillColor:'#16a34a',fillOpacity:.08,dashArray:'4 4'};
  L.geoJSON(r.fc,{style:st,interactive:false}).addTo(hdrLayer);});}
function updateMeas(){var a=D.launches[LCH],h=measHandle.getLatLng(),b=[h.lat,h.lng];
 measLine.setLatLngs([a,b]);measHandle.setTooltipContent(nm(a,b).toFixed(1)+' nm '+comp(brg(a,b)));}
function reanchorMeas(){updateMeas();}
function drawPanel(){var m=D.frames[F].meta;
 var fl=Math.round(m.frac_floating*100),be=Math.round(m.frac_beached*100),sk=Math.round(m.frac_sunk*100);
 var tl=D.frames[F].timeline||[];
 var tHdr='<div style="margin-bottom:7px;font-size:13px">'+m.date+' &middot; <b style="color:#fbbf24">'+m.rel+'</b>'
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FORECAST':'OBSERVED')+'</span></div>'
  +(m.confidence==='forecast'?'<div class=mut style="font-size:11px;margin:-4px 0 7px">forecast drift &mdash; coarse currents, no radar; lower confidence</div>':'');
 var mx=Math.max.apply(null,[0.1].concat(tl.map(function(t){return t.shed;})));
 var tlb=tl.slice().reverse().map(function(t){return '<div style="flex:1;text-align:center" title="Hs '+t.hs_m+' m">'
  +'<div style="height:'+Math.round(26*t.shed/mx)+'px;background:#38bdf8;border-radius:2px 2px 0 0"></div>'
  +'<div style="font-size:9px;color:#94a3b8">'+t.days_ago+'</div></div>';}).join('');
 var best;
 if(m.diffuse){best='<div style="background:#3f2d14;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#fcd34d">Paddies scattered today</b>'
   +'<br/><span class=mut>no tight concentration &mdash; work a wide area &middot; position ±'+(m.pos_pm_nm||0)+' nm</span></div>';}
 else{best='<div style="background:#14532d;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#86efac">Densest paddies'+(m.feature?' near '+m.feature:'')+'</b>'
   +'<br/><span class=mut>primary patch ~'+(m.core_area_km2||0)+' km²'
   +((m.n_patches>1)?' &middot; +'+(m.n_patches-1)+' more patches':'')
   +' &middot; ±'+(m.pos_pm_nm||0)+' nm &middot; widen to ~'+(m.area50_km2||0)+' km² if slow</span></div>';}
 document.getElementById('panel').innerHTML='<div class="panel-head"><span class="chev">▾</span>'+tHdr+best+'</div><div class="panel-body">'
  +'<div class=mut style="margin:-2px 0 7px">Drag the <b style="color:#38bdf8">⊕</b> to measure nm. <b style="color:#fca5a5">Red dots</b> = catch reports — faint until <b>confirmed by multiple anglers</b>. Tap the map then <b style="color:#fca5a5">Log a catch</b> to add yours.</div>'
  +'<div class=mut>est. floating paddies in the Bight</div>'
  +'<div class=score>~'+m.est_floating_paddies.toLocaleString()+'</div>'
  +'<div style="margin:5px 0"><span class=band style="background:'+(BAND[m.abundance_band]||'#64748b')
  +'">'+m.abundance_band+' input</span></div>'
  +'<div class=mut style="margin-bottom:4px">kelp drifting <b style="color:#e2e8f0">'+(m.drift_comp||'?')
  +'</b> ~'+(m.drift_nm||0)+' nm from the beds</div>'
  +'<div class=bars><div style="width:'+fl+'%;background:#f97316"></div>'
  +'<div style="width:'+be+'%;background:#64748b"></div><div style="width:'+sk+'%;background:#1e3a5f"></div></div>'
  +'<div class=mut>'+fl+'% afloat &middot; '+be+'% beached &middot; '+sk+'% sunk</div>'
  +'<div style="margin:8px 0">'+m.why+'</div>'
  +'<div class=mut>Shedding intensity (days ago &rarr;):</div>'
  +'<div style="display:flex;align-items:flex-end;height:34px;gap:2px;margin:3px 0">'+tlb+'</div>'
  +'<div class=leg><span class="sw" style="background:#22c55e"></span>densest'
  +' <span class="sw" style="background:#16a34a;opacity:.55"></span>50%'
  +' <span class="sw" style="background:#16a34a;opacity:.22"></span>80%<br/>'
  +'<span class="sw" style="background:#f59e0b"></span>drift direction'
  +' <span class="sw" style="background:#16a34a"></span>bed'
  +' <span class="sw" style="background:#facc15"></span>your port'
  +' <span class="sw" style="background:#d97706;border-radius:50%"></span>harbor'
  +' <span class="sw" style="background:#06b6d4;border-radius:50%"></span>dive spot'
  +' <span class="sw" style="background:transparent;border:1.5px solid #8b5cf6;border-radius:50%"></span>bank'
  +' <span class="sw" style="background:#38bdf8"></span>ruler (drag ⊕)'
  +' <span class="sw" style="background:#ef4444;border-radius:50%"></span>confirmed catch'
  +' <span class="sw" style="background:#ef4444;border-radius:50%;opacity:.28"></span>unconfirmed'
  +' <span class="sw" style="background:#f59e0b;border-radius:50%;opacity:.5"></span>your pending'
  +'<div class=mut style="margin-top:6px;font-size:11px">'+(D.current_note?D.current_note+'<br/>':'')+D.src_note+'</div></div></div>';}
function setFrame(i){F=Math.max(0,Math.min(D.frames.length-1,i|0));var m=D.frames[F].meta;
 document.getElementById('tlabel').innerHTML='<b>'+m.date+'</b> &middot; '+m.rel
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FC':'OBS')+'</span>';
 drawBase();drawHDR();drawPanel();}
function setL(l){LCH=l;launchMarker.setLatLng(D.launches[LCH]);reanchorMeas();drawHDR();drawPanel();}
function drawReference(){
 // Frame-of-reference overlays — dive spots, harbors/ports, offshore banks.
 // Each in its own layerGroup so the bottom-right layers control toggles them.
 if(spotLayer){spotLayer.clearLayers();((D.reference&&D.reference.spots)||[]).forEach(function(s){
  L.circleMarker([s.lat,s.lng],{radius:3,weight:1,color:'#a5f3fc',fillColor:'#06b6d4',fillOpacity:.6})
   .bindTooltip(s.name,{direction:'top',offset:[0,-3],className:'rep-tip'}).addTo(spotLayer);});}
 if(bankLayer){bankLayer.clearLayers();((D.reference&&D.reference.banks)||[]).forEach(function(b){
  L.circleMarker([b.lat,b.lng],{radius:4,weight:1.5,color:'#c4b5fd',fillColor:'#8b5cf6',fillOpacity:0})
   .bindTooltip(b.name,{direction:'top',offset:[0,-4],className:'rep-tip'}).addTo(bankLayer);});}
 if(portLayer){portLayer.clearLayers();Object.keys(D.launches||{}).forEach(function(n){
  L.circleMarker(D.launches[n],{radius:3.5,weight:1,color:'#fde68a',fillColor:'#d97706',fillOpacity:.7})
   .bindTooltip(n,{direction:'top',offset:[0,-3],className:'rep-tip'})
   .on('click',function(){setL(n);var s=document.getElementById('launch');if(s)s.value=n;}).addTo(portLayer);});}
}

function boot(d){D=d;F=D.default_frame;LCH=D.default_launch;
 map=L.map('map',{zoomControl:true,attributionControl:false}).setView([33.2,-118.5],8);
 var landLayer=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(landLayer);
 }).catch(function(){});
 coneLayer=L.layerGroup().addTo(map);
 hdrLayer=L.layerGroup().addTo(map);
 L.geoJSON(D.beds,{pointToLayer:function(f,ll){return L.circleMarker(ll,{radius:f.properties.island?5:3,
  color:'#052e16',weight:1,fillColor:f.properties.island?'#16a34a':'#4d7c4d',fillOpacity:1}).bindTooltip(f.properties.bed);}}).addTo(map);
 spotLayer=L.layerGroup().addTo(map);bankLayer=L.layerGroup().addTo(map);portLayer=L.layerGroup().addTo(map);
 drawReference();
 L.control.layers(null,{'Dive spots':spotLayer,'Harbors / ports':portLayer,'Offshore banks':bankLayer},{position:'bottomright',collapsed:false}).addTo(map);
 launchMarker=L.circleMarker(D.launches[LCH],{radius:6,color:'#0b1220',weight:2,fillColor:'#facc15',fillOpacity:1}).addTo(map);
 var st=D.launches[LCH];var measEnd=[st[0]-0.05,st[1]-0.35];
 measLine=L.polyline([st,measEnd],{color:'#38bdf8',weight:3,dashArray:'7 6',opacity:.9}).addTo(map);
 measHandle=L.marker(measEnd,{draggable:true,zIndexOffset:1000,icon:L.divIcon({className:'',
  html:'<div class=ruler>⊕</div>',iconSize:[26,26],iconAnchor:[13,13]})}).addTo(map);
 measHandle.bindTooltip('',{permanent:true,direction:'top',offset:[0,-10],className:'meas-label'});
 measHandle.on('drag',updateMeas);
 coordbox=document.getElementById('coordbox');setReadout(map.getCenter());
 map.on('mousemove',function(e){setReadout(e.latlng);});
 map.on('click',function(e){dropWp(e.latlng);});
 map.on('popupopen',wireCopy);
 var panelEl=document.getElementById('panel');panelEl.classList.add('min');
 panelEl.addEventListener('click',function(e){if(e.target.closest('.panel-head'))panelEl.classList.toggle('min');});
 repLayer=L.layerGroup().addTo(map);renderReports();fetchReports();
 document.getElementById('logbtn').onclick=logCatch;
 var tsl=document.getElementById('tslider');
 tsl.max=D.frames.length-1;tsl.value=D.default_frame;
 tsl.oninput=function(){setFrame(parseInt(tsl.value,10));};
 var sel=document.getElementById('launch');
 Object.keys(D.launches).forEach(function(n){var o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o);});
 sel.value=LCH;sel.onchange=function(e){setL(e.target.value);};
 setFrame(D.default_frame);
 updateMeas();}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('panel').innerHTML='<b>Could not load paddy data.</b><br/><span class=mut>data.json failed to fetch.</span>';});
