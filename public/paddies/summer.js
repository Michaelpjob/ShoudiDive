'use strict';
var M,DATA,MODE='both',FI=0,OVA,OVB,PLAY=null;
var MODES=[{k:'y2025',t:'2025'},{k:'elnino',t:'El Niño'},{k:'both',t:'Both'},{k:'diff',t:'Diff'}];
function boot(d){DATA=d;
 M=L.map('map',{zoomControl:false,attributionControl:false}).setView([33.2,-118.7],8);
 L.control.zoom({position:'topright'}).addTo(M);
 fetch('/data/land.geojson',{cache:'force-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(M);
 }).catch(function(){});
 OVA=L.imageOverlay('',DATA.bbox,{opacity:.92,interactive:false}).addTo(M);
 OVB=L.imageOverlay('',DATA.bbox,{opacity:.92,interactive:false}).addTo(M);
 var seg=document.getElementById('mode');
 MODES.forEach(function(m){var b=document.createElement('button');b.textContent=m.t;
  b.className=m.k===MODE?'on':'';
  b.addEventListener('click',function(){MODE=m.k;
   Array.prototype.forEach.call(seg.children,function(c){c.className=c===b?'on':'';});render();});
  seg.appendChild(b);});
 var sc=document.getElementById('scrub');sc.max=d.frames.length-1;
 sc.addEventListener('input',function(){FI=parseInt(sc.value,10);render();});
 document.getElementById('play').addEventListener('click',togglePlay);
 render();}
function togglePlay(){var btn=document.getElementById('play');
 if(PLAY){clearInterval(PLAY);PLAY=null;btn.textContent='▶ play';return;}
 btn.textContent='⏸ pause';PLAY=setInterval(function(){FI=(FI+1)%DATA.frames.length;render();},900);}
function render(){var f=DATA.frames[FI];
 // choose overlay(s): 'both' shows 2025 (cyan) + El Niño (amber); single/diff show one.
 if(MODE==='both'){OVA.setUrl(f.img.y2025);OVB.setUrl(f.img.elnino);OVB.setOpacity(.92);}
 else{OVA.setUrl(f.img[MODE==='diff'?'diff':MODE]);OVB.setUrl(f.img.y2025);OVB.setOpacity(0);}
 document.getElementById('scrub').value=FI;
 document.getElementById('date').textContent=f.date;
 document.getElementById('anom').textContent=(MODE!=='y2025')?('El Niño +'+f.anom+'°C'):'';
 var a=f.read.y2025||{},b=f.read.elnino||{},rows='<tr><td class=mut></td><td class="r a">2025</td><td class="r b">El Niño</td></tr>';
 Object.keys(a).forEach(function(reg){
  rows+='<tr><td>'+reg+'</td><td class="r a">'+(a[reg]?a[reg].shed.toFixed(0):'–')+
   '</td><td class="r b">'+(b[reg]?b[reg].shed.toFixed(0):'–')+'</td></tr>';});
 document.getElementById('tbl').innerHTML=rows;}
fetch('summer.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('hud').innerHTML='<b>Could not load summer.json.</b>';});
