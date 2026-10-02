"""Emit a CSP-clean, externalized site bundle of the Kelp-Paddy Finder for
embedding in ShoudiDive (dev) as a standalone tool — OUTSIDE the main map
overlay.

ShoudiDive is served from Cloudflare Pages with a strict CSP
(`script-src 'self'`, `img-src 'self' data: blob:`). So unlike the proto's
self-contained `out/index.html` (inline <script> + OSM tiles — both blocked
there), this bundle:
  * ships JS as an EXTERNAL file (`app.js`) + self-hosted Leaflet  -> script-src 'self'
  * uses NO external tiles; the basemap is ShoudiDive's own
    `/data/land.geojson` drawn as Leaflet polygons on a dark sea -> img-src clean
  * loads model output from an external `data.json`              -> connect-src 'self'

write_bundle(data, outdir) drops: index.html, app.js, paddies.css,
leaflet.js, leaflet.css, data.json into `outdir` (e.g. ShoudiDive/public/paddies).
"""
from __future__ import annotations

import hashlib
import json
import os

SITE_CSS = """
/* reference overlays — low-key v2 (2026-06-21): bumps the asset hash so the
   restyled paddies.css can't be served stale under a previous ?v= */
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e2e8f0}
#map{position:absolute;inset:0;background:#0b1220}
.bar{position:absolute;z-index:1000;top:10px;left:10px;background:rgba(15,23,42,.94);border:1px solid #1e293b;
 padding:7px 10px;border-radius:9px;font-size:13px;display:flex;align-items:center;gap:8px}
.bar a.back{color:#7dd3fc;text-decoration:none;font-weight:600}
.bar b{color:#e2e8f0}
.beta{background:#0e7490;color:#e0f2fe;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;letter-spacing:.04em}
.tabs{display:inline-flex;gap:4px;margin-left:4px}
.tabs button{background:#1e293b;color:#cbd5e1;border:1px solid #334155;border-radius:6px;padding:3px 8px;cursor:pointer;font-size:12px}
.tabs button.active{background:#f59e0b;color:#0b1220;font-weight:700;border-color:#f59e0b}
.time{display:inline-flex;align-items:center;gap:8px;margin-left:4px}
.time input[type=range]{width:140px;accent-color:#f59e0b;cursor:pointer;vertical-align:middle}
#tlabel{font-size:12px;min-width:116px;display:inline-block}
#tlabel b{color:#fbbf24}
.conf{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;margin-left:5px}
.conf.obs{background:#14532d;color:#86efac} .conf.fc{background:#7c2d12;color:#fdba74}
select{background:#1e293b;color:#cbd5e1;border:1px solid #334155;border-radius:6px;padding:3px 6px}
.panel{position:absolute;z-index:1000;top:10px;right:10px;width:300px;max-height:calc(100% - 20px);overflow:auto;
 background:rgba(15,23,42,.94);border:1px solid #1e293b;color:#e2e8f0;padding:12px 14px;border-radius:9px;font-size:12px;line-height:1.45}
.score{font-size:24px;font-weight:700;line-height:1}
.band{display:inline-block;padding:2px 8px;border-radius:6px;color:#0b1220;font-weight:700;font-size:11px}
.bars{height:8px;border-radius:4px;display:flex;overflow:hidden;margin:6px 0}
.mut{color:#94a3b8}
.ruler{color:#38bdf8;font-size:21px;line-height:24px;text-align:center;cursor:grab;font-weight:700;text-shadow:0 0 4px #000,0 0 2px #000}
.leaflet-tooltip.meas-label{background:#0c4a6e !important;color:#e0f2fe;border:1px solid #38bdf8 !important;
 border-radius:6px;font:700 12px system-ui;padding:2px 7px;white-space:nowrap;box-shadow:0 1px 4px rgba(0,0,0,.5)}
.leaflet-tooltip-top.meas-label::before{border-top-color:#38bdf8}
.leg{margin-top:8px;border-top:1px solid #334155;padding-top:6px}
.sw{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.coordbox{position:absolute;z-index:1000;left:10px;bottom:10px;background:rgba(15,23,42,.94);border:1px solid #1e293b;
 color:#cbd5e1;padding:6px 9px;border-radius:8px;font:12px/1.35 ui-monospace,Menlo,Consolas,monospace;pointer-events:none}
.coordbox b{color:#e2e8f0}
.coordbox .hint{color:#64748b;font-family:system-ui;font-size:11px;margin-top:2px}
.wp{color:#f472b6;font-size:20px;line-height:22px;text-align:center;text-shadow:0 0 4px #000,0 0 3px #000;cursor:grab}
.copybtn{background:#0e7490;color:#e0f2fe;border:none;border-radius:5px;padding:3px 9px;cursor:pointer;font:600 11px system-ui;margin-top:5px}
.leaflet-popup-content .wpc{font:12px/1.4 ui-monospace,Menlo,Consolas,monospace;color:#0b1220}
.logbtn{background:#dc2626;color:#fff;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font:600 12px system-ui}
.leaflet-tooltip.rep-tip{background:rgba(15,23,42,.95) !important;color:#fff;border:1px solid #64748b !important;border-radius:5px;font:600 11px system-ui;padding:2px 7px;box-shadow:0 1px 3px rgba(0,0,0,.5)}
.leaflet-tooltip-top.rep-tip::before{border-top-color:#64748b}
.leaflet-tooltip.ref-lbl{background:none !important;border:none !important;box-shadow:none !important;color:#9fb0c0;font:500 9px/1.1 system-ui;text-shadow:0 1px 2px #000,0 0 2px #000;padding:0;white-space:nowrap;opacity:.72;display:none}
.leaflet-tooltip.ref-lbl::before{display:none !important}
.refz .leaflet-tooltip.ref-lbl{display:block}
.picker{position:absolute;z-index:1200;left:50%;top:54px;transform:translateX(-50%);width:250px;max-height:calc(100% - 70px);overflow:auto;background:rgba(15,23,42,.98);border:1px solid #334155;border-radius:10px;padding:12px 14px;box-shadow:0 6px 24px rgba(0,0,0,.5)}
.picker .ph{font-weight:700;margin-bottom:1px}
.picker .pr{color:#94a3b8;font-size:12px;margin-bottom:6px}
.picker label.pl{display:block;font-size:11px;color:#94a3b8;margin:7px 0 2px}
.picker .req{color:#f87171}
.picker input,.picker select{width:100%;box-sizing:border-box;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 8px;font:13px system-ui}
.picker .pcoord{display:flex;gap:6px}
.picker .pcoord input{flex:1;min-width:0}
.picker .pb{display:flex;gap:8px;margin-top:10px}
.picker .sub{flex:1;background:#16a34a;color:#fff;border:none;border-radius:7px;padding:7px;font:600 13px system-ui;cursor:pointer}
.picker .can{background:#334155;color:#cbd5e1;border:none;border-radius:7px;padding:7px 11px;cursor:pointer;font:600 13px system-ui}
.picker .pn{color:#64748b;font-size:11px;margin-top:8px}
.toast{position:absolute;z-index:1300;left:50%;bottom:18px;transform:translateX(-50%);max-width:80%;text-align:center;background:rgba(15,23,42,.97);border:1px solid #334155;color:#e2e8f0;padding:9px 15px;border-radius:9px;font-size:13px;box-shadow:0 4px 18px rgba(0,0,0,.5)}
.panel-head .chev{display:none}
/* Phone: the panel becomes a collapsible bottom sheet so it never buries the
   map. Collapsed by default (header only); tap the header to expand. */
@media (max-width:680px),(hover:none) and (pointer:coarse){
 .bar{left:6px;right:6px;flex-wrap:wrap;gap:6px 8px;padding:6px 8px}
 .time{margin-left:0}
 .time input[type=range]{width:108px}
 #tlabel{min-width:0;font-size:11px}
 .coordbox{display:none}
 .panel{top:auto;bottom:6px;left:6px;right:6px;width:auto;max-height:52vh;font-size:12px}
 .panel-head{cursor:pointer;position:relative;padding-right:18px}
 .panel-head .chev{display:block;position:absolute;top:0;right:0;color:#94a3b8;font-size:15px;transition:transform .15s}
 .panel.min .panel-body{display:none}
 .panel.min .panel-head .chev{transform:rotate(-90deg)}
}
"""

SITE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex"/>
<title>Kelp Paddy Finder — ShouldIDive (beta)</title>
<link rel="stylesheet" href="leaflet.css"/>
<link rel="stylesheet" href="paddies.css"/>
</head><body>
<div id="map"></div>
<div class="bar"><a class="back" href="/">&larr; ShouldIDive</a>
 <b>Kelp Paddy Finder</b><span class="beta">BETA</span>
 <span class="time"><input type="range" id="tslider" min="0" step="1"/><span id="tlabel"></span></span>
 &nbsp;Launch:&nbsp;<select id="launch"></select>
 &nbsp;<button id="logbtn" class="logbtn" title="Log a catch: tap the map or type GPS coordinates">+ Log a catch</button></div>
<div class="panel" id="panel">Loading paddy model&hellip;</div>
<div class="coordbox" id="coordbox"></div>
<script src="leaflet.js"></script>
<script src="app.js"></script>
</body></html>
"""

# External app.js — fetches data.json + the basemap; no inline script (CSP-safe).
SITE_JS = r"""'use strict';
var LAND_URL='/data/land.geojson';
var BAND={Minimal:'#64748b',Low:'#0ea5e9',Moderate:'#eab308',High:'#f97316',Extreme:'#dc2626'};
// Expected paddy DENSITY by shedding band, anchored to Hobday's measured SCB
// raft density (~1-3 rafts/km2). This is a modelled expectation from shedding +
// drift, NOT a count -- it replaces the old falsely-precise "~N paddies" total.
var DENSITY={
 Minimal:'very few — scattered, no real concentration',
 Low:'sparse — well under 1 paddy/km², even where greenest',
 Moderate:'~1 paddy/km² in the green cores',
 High:'~1–2 paddies/km² in the cores — fresh sheds offshore',
 Extreme:'~2–3 paddies/km² — heavy fresh shedding'};
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
 var TOUCH=!!(L.Browser&&L.Browser.touch);
 var TAPR=TOUCH?18:12;  // invisible tap-target radius: report dots are tiny + hard to hit, esp. on phones
 // One label per report: hover TOOLTIP on desktop, tap POPUP on touch. Binding
 // both showed two identical boxes on phones (the tooltip toggles on tap too).
 function bindRep(m,text){TOUCH?m.bindPopup(text):m.bindTooltip(text,{direction:'top',offset:[0,-4],className:'rep-tip'});return m;}
 REPORTS.forEach(function(r){
  // Age in days since the catch (r.date). Kelp paddies drift, so reports age
  // out: skip anything past the window (the server caps the feed too — this
  // also covers any cached/in-flight older point), then fade + shrink the rest.
  var ageD=r.date?Math.max(0,(nowMs-Date.parse(r.date+'T12:00:00Z'))/864e5):0;
  if(ageD>REPMAXAGE)return;
  var sp=(r.species&&r.species.toLowerCase()!=='paddy')?_cap(r.species):'Reported paddy';
  var conf=r.confidence||'unconfirmed',n=r.sources||1,st,lab;
  if(conf==='unconfirmed'){st={radius:5,weight:1,color:'#fca5a5',fillColor:'#ef4444',fillOpacity:.22,dashArray:'2 3'};lab=sp+' · 1 report · unconfirmed';}
  else if(conf==='strong'){st={radius:8,weight:2.5,color:'#fff',fillColor:'#ef4444',fillOpacity:1};lab=sp+' · confirmed · '+n+' report'+(n>1?'s':'');}
  else{st={radius:6,weight:1.5,color:'#fff',fillColor:'#ef4444',fillOpacity:.92};lab=sp+' · confirmed · '+n+' report'+(n>1?'s':'');}
  // Fade + shrink CONTINUOUSLY with catch age (no full-strength plateau) so a
  // day-old report already reads dimmer than a fresh one — full at age 0,
  // ~0.15 floor by the 7-day edge. A flat plateau made the fade invisible
  // whenever every report was <2 days old.
  var fade=Math.max(.15,1-ageD/7*.85);
  st.fillOpacity=Math.round(st.fillOpacity*fade*100)/100;
  st.radius=Math.max(3,+(st.radius*Math.max(.6,1-ageD/7*.4)).toFixed(1));
  lab+=' · '+(ageD<1?'today':(ageD<2?'1d ago':Math.round(ageD)+'d ago'));
  var rll=[r.lat,r.lng];
  bindRep(L.circleMarker(rll,{radius:TAPR,stroke:false,fillOpacity:0}),lab).addTo(repLayer);
  st.interactive=false;L.circleMarker(rll,st).addTo(repLayer);});
 _mine().forEach(function(r){var lab=(r.species&&r.species.toLowerCase()!=='paddy')?_cap(r.species):'Paddy';
  var mll=[r.lat,r.lng];
  bindRep(L.circleMarker(mll,{radius:TAPR,stroke:false,fillOpacity:0}),lab+' · pending review').addTo(repLayer);
  L.circleMarker(mll,{radius:6,weight:1.5,color:'#fbbf24',fillColor:'#f59e0b',fillOpacity:.45,interactive:false}).addTo(repLayer);});}
function fetchReports(){fetch('/api/paddies/reports',{cache:'no-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  if(!j||!j.reports)return;REPORTS=j.reports;var ap={};REPORTS.forEach(function(r){ap[r.id]=1;});
  _saveMine(_mine().filter(function(m){return !ap[m.id]&&(Date.now()-(m.ts||0))<6048e5;}));
  renderReports();}).catch(function(){});}
function _reporter(){try{return JSON.parse(localStorage.getItem('sd:paddies:reporter')||'{}');}catch(e){return {};}}
function _parseCoord(s){s=(s||'').trim();if(!s)return NaN;var neg=/[swSW]/.test(s)||/^-/.test(s);var n=s.replace(/[^0-9.]+/g,' ').trim().split(' ').map(parseFloat).filter(function(x){return isFinite(x);});if(!n.length)return NaN;var d=n[0]+(n.length>1?n[1]/60:0)+(n.length>2?n[2]/3600:0);return neg?-d:d;}
function logCatch(){
 var ll=wpMarker?wpMarker.getLatLng():null;var box=document.getElementById('picker');
 if(!box){box=document.createElement('div');box.id='picker';box.className='picker';document.body.appendChild(box);}
 var who=_reporter();var v=function(s){return (s||'').replace(/"/g,'&quot;');};
 var opts=SPECIES.map(function(s){return '<option value="'+s+'">'+(s==='paddy'?'Paddy (no fish)':_cap(s))+'</option>';}).join('');
 box.innerHTML='<div class=ph>Log a catch</div>'
  +'<label class=pl>GPS coordinates <span class=req>*</span></label>'
  +'<div class=pcoord><input id=plat placeholder="lat 32.853" value="'+(ll?ll.lat.toFixed(5):'')+'"><input id=plng placeholder="lng -117.270" value="'+(ll?ll.lng.toFixed(5):'')+'"></div>'
  +'<div class=pr>Tap the map to fill these, or type them in: decimal degrees, or deg&nbsp;min like 32 51.18.</div>'
  +'<label class=pl>Email <span class=req>*</span></label><input id=pemail type=email autocomplete=email placeholder="you@example.com" value="'+v(who.email)+'">'
  +'<label class=pl>Name</label><input id=pname maxlength=60 placeholder="optional" value="'+v(who.name)+'">'
  +'<label class=pl>Species</label><select id=spsel>'+opts+'</select>'
  +'<label class=pl>Notes</label><input id=pnotes maxlength=280 placeholder="size, conditions… (optional)">'
  +'<div class=pb><button id=psub class=sub>Submit</button><button id=pcan class=can>Cancel</button></div>'
  +'<div class=pn>Snapped to ~1 nm. Email stays private (moderator only); your pin shows after review.</div>';
 box.style.display='block';
 document.getElementById('pcan').onclick=function(){box.style.display='none';};
 document.getElementById('psub').onclick=function(){
  var lat=_parseCoord(document.getElementById('plat').value),lng=_parseCoord(document.getElementById('plng').value);
  if(!isFinite(lat)||!isFinite(lng)){toast('Enter your GPS coordinates (lat and lng).');return;}
  if(lat<-90||lat>90||lng<-180||lng>180){toast('Those GPS coordinates look invalid.');return;}
  var b=D.bounds;
  if(b&&(lat<b[0][0]||lat>b[1][0]||lng<b[0][1]||lng>b[1][1])){toast('Outside the map area. SoCal longitude is negative, e.g. -117.27.');return;}
  var email=(document.getElementById('pemail').value||'').trim();
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){toast('Please enter a valid email.');return;}
  var name=(document.getElementById('pname').value||'').trim();
  var notes=(document.getElementById('pnotes').value||'').trim();
  try{localStorage.setItem('sd:paddies:reporter',JSON.stringify({email:email,name:name}));}catch(e){}
  box.style.display='none';
  submitReport({lat:lat,lng:lng},document.getElementById('spsel').value,email,name,notes);};}
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
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FORECAST':'HINDCAST')+'</span></div>'
  +'<div class=mut style="font-size:11px;margin:-4px 0 7px">'+(m.confidence==='forecast'
    ?'forecast drift &mdash; coarse currents, no radar; lower confidence'
    :'model estimate from observed HFR + Open-Meteo hindcast inputs')
   +(m.build_utc?' &middot; built '+m.build_utc.slice(0,16).replace('T',' ')+' UTC':'')+'</div>';
 var mx=Math.max.apply(null,[0.1].concat(tl.map(function(t){return t.shed;})));
 var tlb=tl.slice().reverse().map(function(t){return '<div style="flex:1;text-align:center" title="Hs '+t.hs_m+' m">'
  +'<div style="height:'+Math.round(26*t.shed/mx)+'px;background:#38bdf8;border-radius:2px 2px 0 0"></div>'
  +'<div style="font-size:9px;color:#94a3b8">'+t.days_ago+'</div></div>';}).join('');
 var best;
 if(m.diffuse){best='<div style="background:#3f2d14;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#fcd34d">Paddies scattered today</b>'
   +'<br/><span class=mut>no tight concentration &mdash; work a wide area &middot; position ±'+(m.pos_pm_nm||0)+' nm</span></div>';}
 else{best='<div style="background:#14532d;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#86efac">Highest paddy-likelihood'+(m.feature?' near '+m.feature:'')+'</b>'
   +'<br/><span class=mut>primary patch ~'+(m.core_area_km2||0)+' km²'
   +((m.n_patches>1)?' &middot; +'+(m.n_patches-1)+' more patches':'')
   +' &middot; ±'+(m.pos_pm_nm||0)+' nm &middot; widen to ~'+(m.area50_km2||0)+' km² if slow</span></div>';}
 document.getElementById('panel').innerHTML='<div class="panel-head"><span class="chev">▾</span>'+tHdr+best+'</div><div class="panel-body">'
  +'<div class=mut style="margin:-2px 0 7px">Green = <b>model-estimated paddy likelihood</b>, not confirmed paddies. Drag the <b style="color:#38bdf8">⊕</b> to measure nm. <b style="color:#fca5a5">Red dots</b> = catch reports — faint until <b>confirmed by corroboration or a trusted reporter</b>. Tap the map then <b style="color:#fca5a5">Log a catch</b> to add yours.</div>'
  +'<div class=mut>expected paddy density &middot; where it&rsquo;s greenest</div>'
  +'<div style="font-size:15px;font-weight:600;color:#86efac;margin:2px 0 5px">'+(DENSITY[m.abundance_band]||DENSITY.Low)+'</div>'
  +'<div style="margin:5px 0"><span class=band style="background:'+(BAND[m.abundance_band]||'#64748b')
  +'">'+m.abundance_band+' shedding</span> <span class=mut style="font-size:11px">&middot; modelled, not counted (Hobday SCB density)</span></div>'
  +'<div class=mut style="margin-bottom:4px">kelp drifting <b style="color:#e2e8f0">'+(m.drift_comp||'?')
  +'</b> ~'+(m.drift_nm||0)+' nm from the beds</div>'
  +'<div class=bars><div style="width:'+fl+'%;background:#f97316"></div>'
  +'<div style="width:'+be+'%;background:#64748b"></div><div style="width:'+sk+'%;background:#1e3a5f"></div></div>'
  +'<div class=mut>'+fl+'% afloat &middot; '+be+'% beached &middot; '+sk+'% sunk</div>'
  +'<div style="margin:8px 0">'+m.why+'</div>'
  +'<div class=mut>Shedding intensity (days ago &rarr;):</div>'
  +'<div style="display:flex;align-items:flex-end;height:34px;gap:2px;margin:3px 0">'+tlb+'</div>'
  +'<div class=leg><span class="sw" style="background:#22c55e"></span>likeliest'
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
  +'<div class=mut style="margin-top:6px;font-size:11px">'+(D.current_note?D.current_note+'<br/>':'')+D.src_note+'</div>'
  +'<div class=mut style="margin-top:6px;font-size:11px;color:#fbbf24">⚠ Planning aid only &mdash; check weather, swell, fuel range, and closures before heading offshore.</div></div></div>';}
function setFrame(i){F=Math.max(0,Math.min(D.frames.length-1,i|0));var m=D.frames[F].meta;
 document.getElementById('tlabel').innerHTML='<b>'+m.date+'</b> &middot; '+m.rel
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FC':'HIND')+'</span>';
 drawBase();drawHDR();drawPanel();}
function setL(l){LCH=l;launchMarker.setLatLng(D.launches[LCH]);reanchorMeas();drawHDR();drawPanel();}
function drawReference(){
 // Frame-of-reference overlays — dive spots, harbors/ports, offshore banks.
 // Each in its own layerGroup so the bottom-right layers control toggles them.
 // Deliberately low-key: small, muted markers (cf. the main app's quiet pins).
 // Labels are subtle + zoom-gated (.ref-lbl hidden until refz() adds .refz at
 // zoom ≥ 9) so the overview stays calm and names appear when you zoom in.
 var LBL={permanent:true,direction:'right',offset:[5,0],className:'ref-lbl'};
 if(spotLayer){spotLayer.clearLayers();((D.reference&&D.reference.spots)||[]).forEach(function(s){
  L.circleMarker([s.lat,s.lng],{radius:2.5,weight:1,opacity:.5,color:'#7dd3fc',fillColor:'#0e7490',fillOpacity:.35})
   .bindTooltip(s.name,LBL).addTo(spotLayer);});}
 if(bankLayer){bankLayer.clearLayers();((D.reference&&D.reference.banks)||[]).forEach(function(b){
  L.circleMarker([b.lat,b.lng],{radius:3,weight:1,opacity:.6,color:'#a78bfa',fillColor:'#8b5cf6',fillOpacity:0})
   .bindTooltip(b.name,LBL).addTo(bankLayer);});}
 if(portLayer){portLayer.clearLayers();Object.keys(D.launches||{}).forEach(function(n){
  L.circleMarker(D.launches[n],{radius:2.5,weight:1,opacity:.5,color:'#fbbf24',fillColor:'#b45309',fillOpacity:.35})
   .bindTooltip(n,LBL)
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
 // Kelp source beds, sized by how much each is shedding right now (detach_now)
 // so the actively-shedding forests read loudest. Shore (mainland) beds get a
 // bright emerald + a floor size so the local shoreline kelp (Palos Verdes,
 // La Jolla, Laguna) is actually visible, not a tiny grey dot.
 L.geoJSON(D.beds,{pointToLayer:function(f,ll){var p=f.properties,sh=p.detach_now||0;
  var r=(p.island?4:4.5)+Math.min(4,sh*7);
  var lab=(p.island?'Island':'Shore')+' kelp bed · shedding '+(sh<0.2?'low':sh<0.4?'moderate':'high');
  return L.circleMarker(ll,{radius:r,color:'#052e16',weight:1,
   fillColor:p.island?'#16a34a':'#34d399',fillOpacity:.92}).bindTooltip(lab);}}).addTo(map);
 spotLayer=L.layerGroup().addTo(map);bankLayer=L.layerGroup().addTo(map);portLayer=L.layerGroup().addTo(map);
 drawReference();
 L.control.layers(null,{'Dive spots':spotLayer,'Harbors / ports':portLayer,'Offshore banks':bankLayer},{position:'bottomright',collapsed:true}).addTo(map);
 var refz=function(){map.getContainer().classList.toggle('refz',map.getZoom()>=9);};
 map.on('zoomend',refz);refz();
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
"""


def _read_vendor(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", name)
    with open(p, encoding="utf-8") as f:
        return f.read()


def write_bundle(data, outdir, land_url="/data/land.geojson"):
    """Write the externalized, CSP-clean tool bundle into `outdir`.

    The asset URLs in index.html are version-stamped (`app.js?v=<hash>`). The
    files are unhashed and Cloudflare's zone Browser-Cache-TTL pins .js/.css for
    ~4 h regardless of our _headers, so without this a returning user stays on
    the old UI for hours. index.html itself is always-fresh (dynamic), so a new
    ?v on a deploy points them at the new files on a normal reload."""
    os.makedirs(outdir, exist_ok=True)
    js = SITE_JS.replace("/data/land.geojson", land_url)
    css, lcss, ljs = SITE_CSS, _read_vendor("leaflet.css"), _read_vendor("leaflet.js")
    ver = hashlib.md5((js + css + lcss + ljs).encode("utf-8")).hexdigest()[:8]
    html = (SITE_HTML
            .replace('href="leaflet.css"', f'href="leaflet.css?v={ver}"')
            .replace('href="paddies.css"', f'href="paddies.css?v={ver}"')
            .replace('src="leaflet.js"', f'src="leaflet.js?v={ver}"')
            .replace('src="app.js"', f'src="app.js?v={ver}"'))
    files = {
        "index.html": html,
        "paddies.css": css,
        "app.js": js,
        "leaflet.css": lcss,
        "leaflet.js": ljs,
        "data.json": json.dumps(data, separators=(",", ":")),
    }
    for name, content in files.items():
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            f.write(content)
    return outdir
