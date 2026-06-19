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

var D,S,LCH,map,coneLayer,hdrLayer,launchMarker,measLine,measHandle;

function drawBase(){coneLayer.clearLayers();
 L.geoJSON({type:'FeatureCollection',features:D.scenarios[S].cones},{style:function(f){return {color:'#f59e0b',weight:1,
  opacity:.2+.3*f.properties.opacity,fillColor:'#f59e0b',fillOpacity:.03+.14*f.properties.opacity};}})
  .bindPopup(function(l){var p=l.feature.properties;return '<b>'+p.bed+'</b><br/>kelp drifting <b>'+p.compass
   +'</b> ('+p.bearing+'°), reach ~'+p.reach_nm+' nm';}).addTo(coneLayer);}
function drawHDR(){hdrLayer.clearLayers();var h=D.scenarios[S].hdr;if(!h||!h.regions)return;
 h.regions.slice().sort(function(a,b){return b.level-a.level;}).forEach(function(r){
  var core=r.level<=0.35,mid=r.level>0.35&&r.level<=0.6;
  var st=core?{color:'#86efac',weight:2,fillColor:'#22c55e',fillOpacity:.55}
         :mid?{color:'#4ade80',weight:1.5,fillColor:'#16a34a',fillOpacity:.22}
         :{color:'#4ade80',weight:1,fillColor:'#16a34a',fillOpacity:.08,dashArray:'4 4'};
  L.geoJSON(r.fc,{style:st}).bindTooltip(core?'densest (~'+Math.round(r.level*100)+'% of paddies)'
   :Math.round(r.level*100)+'% of paddies').addTo(hdrLayer);});}
function updateMeas(){var a=D.launches[LCH],h=measHandle.getLatLng(),b=[h.lat,h.lng];
 measLine.setLatLngs([a,b]);measHandle.setTooltipContent(nm(a,b).toFixed(1)+' nm '+comp(brg(a,b)));}
function reanchorMeas(){updateMeas();}
function drawPanel(){var m=D.scenarios[S].meta;
 var fl=Math.round(m.frac_floating*100),be=Math.round(m.frac_beached*100),sk=Math.round(m.frac_sunk*100);
 var tl=D.scenarios[S].timeline||[];
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
 document.getElementById('panel').innerHTML=best
  +'<div class=mut style="margin:-2px 0 7px">Drag the <b style="color:#38bdf8">⊕</b> to measure nm from your port.</div>'
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
  +' <span class="sw" style="background:#38bdf8"></span>ruler (drag ⊕)'
  +'<div class=mut style="margin-top:6px;font-size:11px">'+(D.current_note?D.current_note+'<br/>':'')+D.src_note+'</div></div>';}
function setS(s){S=s;
 document.querySelectorAll('#tabs button').forEach(function(b){b.classList.toggle('active',b.dataset.s===s);});
 drawBase();drawHDR();drawPanel();}
function setL(l){LCH=l;launchMarker.setLatLng(D.launches[LCH]);reanchorMeas();drawHDR();drawPanel();}

function boot(d){D=d;S=D.order[0];LCH=D.default_launch;
 map=L.map('map',{zoomControl:true,attributionControl:false}).setView([33.2,-118.5],8);
 var landLayer=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(landLayer);
 }).catch(function(){});
 coneLayer=L.layerGroup().addTo(map);
 hdrLayer=L.layerGroup().addTo(map);
 L.geoJSON(D.beds,{pointToLayer:function(f,ll){return L.circleMarker(ll,{radius:f.properties.island?5:3,
  color:'#052e16',weight:1,fillColor:f.properties.island?'#16a34a':'#4d7c4d',fillOpacity:1}).bindTooltip(f.properties.bed);}}).addTo(map);
 launchMarker=L.circleMarker(D.launches[LCH],{radius:6,color:'#0b1220',weight:2,fillColor:'#facc15',fillOpacity:1}).addTo(map);
 var st=D.launches[LCH];var measEnd=[st[0]-0.05,st[1]-0.35];
 measLine=L.polyline([st,measEnd],{color:'#38bdf8',weight:3,dashArray:'7 6',opacity:.9}).addTo(map);
 measHandle=L.marker(measEnd,{draggable:true,zIndexOffset:1000,icon:L.divIcon({className:'',
  html:'<div class=ruler>⊕</div>',iconSize:[26,26],iconAnchor:[13,13]})}).addTo(map);
 measHandle.bindTooltip('',{permanent:true,direction:'top',offset:[0,-10],className:'meas-label'});
 measHandle.on('drag',updateMeas);
 var tabs=document.getElementById('tabs');
 D.order.forEach(function(s){var b=document.createElement('button');b.dataset.s=s;b.textContent=LABEL[s]||s;
  b.onclick=function(){setS(s);};tabs.appendChild(b);});
 var sel=document.getElementById('launch');
 Object.keys(D.launches).forEach(function(n){var o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o);});
 sel.value=LCH;sel.onchange=function(e){setL(e.target.value);};
 var hsh=(location.hash||'').replace('#','');setS(D.order.indexOf(hsh)>=0?hsh:S);
 window.addEventListener('hashchange',function(){var x=(location.hash||'').replace('#','');if(D.order.indexOf(x)>=0)setS(x);});
 updateMeas();}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('panel').innerHTML='<b>Could not load paddy data.</b><br/><span class=mut>data.json failed to fetch.</span>';});
