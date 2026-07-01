'use strict';
var M,DATA,SC,FI=0,OV,PLAY=null;
function boot(d){DATA=d;SC=d.scenarios[0].tag;
 M=L.map('map',{zoomControl:false,attributionControl:false}).setView([33.2,-118.7],8);
 L.control.zoom({position:'topright'}).addTo(M);
 fetch('/data/land.geojson',{cache:'force-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(M);
 }).catch(function(){});
 OV=L.imageOverlay('',DATA.bbox,{opacity:.9,interactive:false}).addTo(M);
 var seg=document.getElementById('scen');
 d.scenarios.forEach(function(s,i){var b=document.createElement('button');b.textContent=s.label;
  b.className=i===0?'on':'';b.setAttribute('data-t',s.tag);
  b.addEventListener('click',function(){SC=s.tag;
   Array.prototype.forEach.call(seg.children,function(c){c.className=c===b?'on':'';});render();});
  seg.appendChild(b);});
 var sc=document.getElementById('scrub');sc.max=d.frames.length-1;
 sc.addEventListener('input',function(){FI=parseInt(sc.value,10);render();});
 document.getElementById('play').addEventListener('click',togglePlay);
 render();}
function togglePlay(){var btn=document.getElementById('play');
 if(PLAY){clearInterval(PLAY);PLAY=null;btn.textContent='▶ play';return;}
 btn.textContent='⏸ pause';PLAY=setInterval(function(){FI=(FI+1)%DATA.frames.length;
  document.getElementById('scrub').value=FI;render();},900);}
function render(){var f=DATA.frames[FI];
 OV.setUrl(f.img[SC]);
 document.getElementById('scrub').value=FI;
 document.getElementById('date').textContent=f.date;
 var a=document.getElementById('anom');
 a.textContent=SC==='elnino'?('El Niño +'+f.anom+'°C'):'';
 var rd=f.read[SC]||{},rows='';
 Object.keys(rd).forEach(function(reg){var v=rd[reg];
  rows+='<tr><td>'+reg+'</td><td class=r>full '+v.full.toFixed(2)+
   '</td><td class=r>shed '+v.shed.toFixed(0)+'</td></tr>';});
 document.getElementById('tbl').innerHTML=rows;}
fetch('summer.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('hud').innerHTML='<b>Could not load summer.json.</b>';});
