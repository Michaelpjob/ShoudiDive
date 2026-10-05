'use strict';
/* Fleet Tracks: where the SoCal sportfishing fleet actually stops, from public
   AIS. Everything here aggregates client-side from data.json's stop list, so
   the boat / week / stop-kind filters are instant. CSP-clean: same-origin only. */
var LAND_URL='/data/land.geojson';
var CONV_BOATS=3;          // "fleet convergence" = this many boats in one cell on one day
var D,map,hexLayer,stopLayer,portLayer,STOPWEEK=[],WEEKIDX={};
var S={week:-1,metric:'fish',boat:'all',kind:'all',nearshore:false,cell:null,trip:null,playing:null};

function fmtDate(s){var d=new Date(s+'T12:00:00Z');return d.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});}
function hrs(m){return (m/60).toFixed(m<600?1:0)+' h';}
function pct(x){return x==null?'—':Math.round(x*100)+'%';}
function short(l){return String(l||'').replace(/ Sportfishing| Landing| Sea Center/g,'').replace("Davey's Locker","Davey's");}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}

/* sequential ramp (dark slate -> amber -> red), value in 0..1 */
var RAMP=[[30,41,59],[120,53,15],[217,119,6],[251,191,36],[254,240,138]];
function color(v){v=Math.max(0,Math.min(1,v));var p=v*(RAMP.length-1),i=Math.floor(p),f=p-i;var a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];
 return 'rgb('+Math.round(a[0]+(b[0]-a[0])*f)+','+Math.round(a[1]+(b[1]-a[1])*f)+','+Math.round(a[2]+(b[2]-a[2])*f)+')';}

function boatMatch(bi){if(S.boat==='all')return true;
 if(S.boat.slice(0,2)==='L:')return D.boats[bi].landing===S.boat.slice(2);
 if(S.boat.slice(0,2)==='P:')return D.boats[bi].port===S.boat.slice(2);
 return bi===+S.boat;}

/* Aggregate the filtered stops per cell. */
function aggregate(){
 var A={};
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];
  if(!boatMatch(s[0]))continue;
  if(S.kind!=='all'&&s[5]!==+S.kind)continue;
  if(!S.nearshore&&s[8])continue;
  if(S.week>=0&&STOPWEEK[i]!==S.week)continue;
  var h=s[6],c=A[h];if(!c){c=A[h]={fish:0,troll:0,stops:0,trips:{},boats:{},days:{},byBoat:{}};}
  c.fish+=s[4];if(s[5]===1)c.troll+=s[4];c.stops++;
  c.trips[s[0]+':'+s[7]]=1;c.boats[s[0]]=1;
  (c.days[s[1]]=c.days[s[1]]||{})[s[0]]=1;
  c.byBoat[s[0]]=(c.byBoat[s[0]]||0)+s[4];}
 Object.keys(A).forEach(function(h){var c=A[h];c.nTrips=Object.keys(c.trips).length;c.nBoats=Object.keys(c.boats).length;
  c.conv=0;Object.keys(c.days).forEach(function(d){if(Object.keys(c.days[d]).length>=CONV_BOATS)c.conv++;});
  c.nDays=Object.keys(c.days).length;});
 return A;}

function metricOf(c,h){switch(S.metric){
 case 'boats':return c.nBoats;case 'trips':return c.nTrips;case 'conv':return c.conv;
 case 'cpa':var k=D.cell_catch[h];return k?k[0]:0;default:return c.fish;}}
function metricLabel(){return {fish:'fishing minutes',boats:'distinct boats',trips:'distinct trips',conv:'fleet-convergence days (≥'+CONV_BOATS+' boats, same day)',cpa:'fish kept per angler (trips with a dock count)'}[S.metric];}

function draw(){
 var A=aggregate();hexLayer.clearLayers();
 var vals=[];Object.keys(A).forEach(function(h){var v=metricOf(A[h],h);if(v>0)vals.push(v);});
 vals.sort(function(a,b){return a-b;});
 var vmax=vals.length?vals[Math.floor((vals.length-1)*0.97)]:1;  // clip the top 3% so one mega-cell doesn't flatten the ramp
 if(vmax<=0)vmax=1;
 Object.keys(A).forEach(function(h){var c=A[h],v=metricOf(c,h);if(v<=0||!D.cells[h])return;
  var t=S.metric==='fish'?Math.sqrt(v/vmax):v/vmax;
  var poly=L.polygon(D.cells[h].b,{color:h===S.cell?'#fff':'#0b1220',weight:h===S.cell?2:.5,fillColor:color(t),fillOpacity:.72,interactive:true});
  poly.bindTooltip(tipHtml(h,c),{className:'cell-tip',sticky:true,direction:'top',offset:[0,-6]});
  poly.on('click',function(){selectCell(h);});
  poly.addTo(hexLayer);});
 drawStops();
 document.getElementById('legend').innerHTML=legendHtml(vmax);
 if(S.cell&&!A[S.cell]){S.cell=null;}
 renderPanel(A);}

function tipHtml(h,c){var top=Object.keys(c.byBoat).map(function(b){return [b,c.byBoat[b]];}).sort(function(a,b){return b[1]-a[1];}).slice(0,4);
 var k=D.cell_catch[h];
 return '<b>'+hrs(c.fish)+'</b> fishing · '+c.nBoats+' boat'+(c.nBoats>1?'s':'')+' · '+c.nTrips+' trip'+(c.nTrips>1?'s':'')+' · '+c.nDays+' day'+(c.nDays>1?'s':'')
  +(c.conv?'<br/><span style="color:#fbbf24">'+c.conv+' convergence day'+(c.conv>1?'s':'')+'</span>':'')
  +(c.troll?'<br/>'+Math.round(100*c.troll/c.fish)+'% trolling':'')
  +(k?'<br/>'+k[0]+' fish/angler on '+k[1]+' counted trip'+(k[1]>1?'s':''):'')
  +'<br/><span class="mut">'+top.map(function(t){return esc(D.boats[t[0]].name)+' '+hrs(t[1]);}).join(' · ')+'</span>'
  +'<br/><span class="mut">click for detail</span>';}

function legendHtml(vmax){var steps=[0,.25,.5,.75,1];var f=S.metric==='fish'?function(t){return hrs(t*t*vmax);}:function(t){return (Math.round(t*vmax*10)/10)+'';};
 return '<div>'+metricLabel()+(S.week>=0?' · week of '+fmtDate(D.weeks[S.week]):' · whole period')+'</div>'
  +steps.map(function(t){return '<span class="sw" style="background:'+color(t)+'"></span>'+f(t)+'&nbsp;&nbsp;';}).join('')
  +'<div>cells ≈ '+(D.meta.h3_res===7?'1.4 km':'0.5 km')+' across (H3 res '+D.meta.h3_res+')</div>';}

function drawStops(){stopLayer.clearLayers();
 var showAll=map.getZoom()>=10,single=S.boat!=='all'&&S.boat.slice(0,2)!=='L:'&&S.boat.slice(0,2)!=='P:';
 if(!showAll&&!single)return;
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];
  if(!boatMatch(s[0]))continue;if(S.kind!=='all'&&s[5]!==+S.kind)continue;
  if(!S.nearshore&&s[8])continue;if(S.week>=0&&STOPWEEK[i]!==S.week)continue;
  if(S.trip!=null&&!(s[0]===S.trip[0]&&s[7]===S.trip[1]))continue;
  var r=Math.max(2,Math.min(9,Math.sqrt(s[4]/8)));
  L.circleMarker([s[2],s[3]],{radius:r,weight:s[5]===1?1.5:0.5,color:s[5]===1?'#38bdf8':'#fff',fillColor:s[5]===1?'#0ea5e9':'#f8fafc',fillOpacity:.55})
   .bindTooltip(esc(D.boats[s[0]].name)+' · '+fmtDate(s[1])+' · '+hrs(s[4])+(s[5]===1?' trolling':' drift/anchor'),{className:'cell-tip',direction:'top'})
   .addTo(stopLayer);}}

function selectCell(h){S.cell=(S.cell===h)?null:h;draw();}

function renderPanel(A){var p=document.getElementById('panel');
 if(!p._wired){p._wired=true;p.addEventListener('click',function(e){if(e.target.closest('h3')&&!e.target.closest('a'))p.classList.toggle('min');});}
 if(S.cell&&A[S.cell]){p.innerHTML=cellPanel(S.cell,A[S.cell]);return;}
 if(S.boat!=='all'&&S.boat.slice(0,2)!=='L:'&&S.boat.slice(0,2)!=='P:'){p.innerHTML=boatPanel(+S.boat);wireTrips();return;}
 p.innerHTML=fleetPanel();wireRows();}

function fleetPanel(){var m=D.meta,s=D.summary;
 var rows=D.boats.map(function(b,i){return [i,b];}).filter(function(x){return boatMatch(x[0])&&x[1].trips>0;})
  .sort(function(a,b){return b[1].fish_min-a[1].fish_min;});
 var html='<h3>The SoCal sportfishing fleet, from AIS</h3>'
  +'<div class="mut">'+m.n_boats+' boats · '+m.n_trips+' trips · '+m.n_stops+' fishing stops · '+fmtDate(m.start)+' – '+fmtDate(m.end)+' '+m.end.slice(0,4)+'</div>'
  +'<h4>Three questions</h4>'
  +'<div class="ans"><div class="q">Does a boat go back to the same spot next trip?</div><div class="a">Median boat puts <b>'+pct(s.return_same_spot_median)+'</b> of its next trip\'s fishing time in the same 0.5 km cells, <b>'+pct(s.return_same_area_median)+'</b> within ~1.5 km of them ('+(s.trip_pairs||0)+' trip pairs).</div></div>'
  +'<div class="ans"><div class="q">Does it work a home zone?</div><div class="a">Median boat has <b>'+pct(s.home_top_share_median)+'</b> of its season in one '+(m.h3_res===7?'1.4':'0.5')+' km cell and needs <b>'+(s.home_cells_for_half_median||'—')+'</b> cells to cover half its fishing time.</div></div>'
  +'<div class="ans"><div class="q">Does the fleet converge on a bite?</div><div class="a"><b>'+(s.convergence_days_total||0)+'</b> boat-days had ≥'+CONV_BOATS+' fleet boats in the same cell; median boat spends <b>'+pct(s.convergence_share_median)+'</b> of its fishing days in a convergence.</div></div>'
  +'<h4>Boats <span class="mut">(click one)</span></h4><table class="boats"><tr><th>Boat</th><th class="n">Trips</th><th class="n">Fishing</th><th class="n" title="share of the next trip within 1.5 km of the previous trip">Same area</th><th class="n" title="share of the season in the top cell">Home</th><th class="n" title="share of trip time with an AIS position">AIS</th></tr>';
 rows.forEach(function(x){var b=x[1];html+='<tr class="row" data-b="'+x[0]+'"><td>'+esc(b.name)+'<br/><span class="mut">'+esc(short(b.landing))+'</span></td><td class="n">'+b.trips+'</td><td class="n">'+hrs(b.fish_min)+'</td><td class="n">'+pct(b.return_same_area)+'</td><td class="n">'+pct(b.top_share)+'</td><td class="n">'+pct(b.coverage)+'</td></tr>';});
 html+='</table><div class="caveat">Source: USCG NAIS via NOAA MarineCadastre, one position a minute from shore receivers, so tracks fade beyond roughly 30–50 nm and the long-range fleet is mostly seen leaving and returning ("AIS cov." is the share of each trip with a position). A stop is a run below '+m.stop_rule.drift_max_kt+' kt for ≥'+m.stop_rule.drift_min_min+' min, or a looping run at '+m.stop_rule.troll_kt[0]+'–'+m.stop_rule.troll_kt[1]+' kt for ≥'+m.stop_rule.troll_min_min+' min. Stops within '+m.harbor_km+' km of a landing are never counted; stops within '+m.nearshore_km+' km (bait grounds, the kelp edge) are hidden unless you tick the box.'
  +(m.count_joined_trips?' Dock counts joined for '+m.count_joined_trips+' trips (sportfishingreport.com).':'')+'</div>';
 return html;}

function boatPanel(i){var b=D.boats[i];
 var trips=D.trips.filter(function(t){return t[0]===i;}).sort(function(a,c){return a[1]<c[1]?-1:1;});
 var html='<h3>'+esc(b.name)+' <span class="mut">· '+esc(b.landing)+'</span></h3>'
  +'<div class="mut">'+(b.length_ft?b.length_ft+' ft · ':'')+(b.trip_type?esc(b.trip_type)+' · ':'')+'MMSI '+b.mmsi+'</div>'
  +'<div class="stat"><span>Trips seen</span><b>'+b.trips+'</b></div>'
  +'<div class="stat"><span>Fishing time</span><b>'+hrs(b.fish_min)+'</b></div>'
  +'<div class="stat"><span>Farthest from dock</span><b>'+(b.max_km?Math.round(b.max_km/1.852)+' nm':'—')+'</b></div>'
  +'<div class="stat"><span>AIS coverage of trips</span><b>'+pct(b.coverage)+'</b></div>'
  +'<div class="stat"><span>Next trip, same 0.5 km spot / same area</span><b>'+pct(b.return_same_spot)+' / '+pct(b.return_same_area)+'</b></div>'
  +'<div class="stat"><span>Home zone: top cell share · cells for half</span><b>'+pct(b.top_share)+' · '+(b.cells_for_half||'—')+'</b></div>'
  +'<div class="stat"><span>Fishing days in a fleet convergence</span><b>'+pct(b.convergence_share)+'</b></div>'
  +'<h4>Trips <span class="mut">(click to isolate its stops)</span></h4><table><tr><th>Left</th><th class="n">Hours</th><th class="n">Max nm</th><th class="n">Stops</th><th class="n">Fishing</th><th class="n">Dock count</th></tr>';
 trips.forEach(function(t){var sel=S.trip&&S.trip[0]===i&&S.trip[1]===t[13];
  html+='<tr class="row'+(sel?' sel':'')+'" data-t="'+t[13]+'"><td>'+fmtDate(t[1])+'</td><td class="n">'+t[2]+'</td><td class="n">'+Math.round(t[3]/1.852)+'</td><td class="n">'+t[5]+'</td><td class="n">'+hrs(t[6])+'</td><td class="n">'+(t[10]!=null?t[10]+' fish / '+t[9]+' anglers':'<span class="mut">—</span>')+'</td></tr>';});
 html+='</table><div class="caveat">A dock count is the landing\'s posted total for that boat on the day it returned; multi-day trips get one count for all their stops.</div>';
 return html;}

function cellPanel(h,c){var ctr=D.cells[h].c;
 var byBoat=Object.keys(c.byBoat).map(function(b){return [b,c.byBoat[b]];}).sort(function(a,b){return b[1]-a[1];});
 var days=Object.keys(c.days).sort();
 var k=D.cell_catch[h];
 var html='<h3>Cell '+ctr[0].toFixed(3)+', '+ctr[1].toFixed(3)+' <a href="#" id="unsel">✕</a></h3>'
  +'<div class="stat"><span>Fishing time</span><b>'+hrs(c.fish)+(c.troll?' ('+Math.round(100*c.troll/c.fish)+'% trolling)':'')+'</b></div>'
  +'<div class="stat"><span>Boats · trips · days</span><b>'+c.nBoats+' · '+c.nTrips+' · '+c.nDays+'</b></div>'
  +'<div class="stat"><span>Fleet-convergence days</span><b>'+c.conv+'</b></div>'
  +(k?'<div class="stat"><span>Fish kept per angler (counted trips)</span><b>'+k[0]+' on '+k[1]+'</b></div>':'')
  +'<h4>Boats here</h4><table>';
 byBoat.slice(0,12).forEach(function(x){html+='<tr><td>'+esc(D.boats[x[0]].name)+'</td><td class="mut">'+esc(short(D.boats[x[0]].landing))+'</td><td class="n">'+hrs(x[1])+'</td></tr>';});
 html+='</table><h4>Days</h4><div class="mut" style="line-height:1.7">'+days.map(function(d){var n=Object.keys(c.days[d]).length;return '<span'+(n>=CONV_BOATS?' style="color:#fbbf24"':'')+'>'+fmtDate(d)+(n>1?' ×'+n:'')+'</span>';}).join(' · ')+'</div>';
 setTimeout(function(){var u=document.getElementById('unsel');if(u)u.onclick=function(e){e.preventDefault();S.cell=null;draw();};},0);
 return html;}

function wireRows(){document.querySelectorAll('#panel tr.row').forEach(function(tr){tr.onclick=function(){setBoat(tr.getAttribute('data-b'));};});}
function wireTrips(){document.querySelectorAll('#panel tr.row').forEach(function(tr){tr.onclick=function(){var tid=+tr.getAttribute('data-t'),bi=+S.boat;
  S.trip=(S.trip&&S.trip[1]===tid)?null:[bi,tid];
  if(S.trip){var t=D.trips.filter(function(x){return x[0]===bi&&x[13]===tid;})[0];if(t)map.panTo([t[7],t[8]]);}
  draw();};});}
function setBoat(v){S.boat=v;S.cell=null;S.trip=null;document.getElementById('boat').value=v;draw();}

function setWeek(i){S.week=i;document.getElementById('week').value=i;
 document.getElementById('wlabel').textContent=i<0?'Whole period':'Week of '+fmtDate(D.weeks[i])+' '+D.weeks[i].slice(0,4);draw();}
function togglePlay(){var b=document.getElementById('play');
 if(S.playing){clearInterval(S.playing);S.playing=null;b.innerHTML='&#9654;';return;}
 b.innerHTML='&#10074;&#10074;';if(S.week<0||S.week>=D.weeks.length-1)setWeek(0);
 S.playing=setInterval(function(){if(S.week>=D.weeks.length-1){togglePlay();return;}setWeek(S.week+1);},1100);}

function boot(d){D=d;
 D.weeks.forEach(function(w,i){WEEKIDX[w]=i;});
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];
  var dt=new Date(s[1]+'T12:00:00Z');var mon=new Date(dt);mon.setUTCDate(dt.getUTCDate()-((dt.getUTCDay()+6)%7));
  var key=mon.toISOString().slice(0,10);STOPWEEK[i]=WEEKIDX[key]==null?-2:WEEKIDX[key];}
 map=L.map('map',{zoomControl:true,attributionControl:false});
 map.fitBounds([[32.4,-119.7],[34.0,-116.9]],{paddingTopLeft:[0,70],paddingBottomRight:[window.innerWidth>760?380:0,0]});
 var landLayer=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(landLayer);}).catch(function(){});
 hexLayer=L.layerGroup().addTo(map);stopLayer=L.layerGroup().addTo(map);portLayer=L.layerGroup().addTo(map);
 Object.keys(D.landings).forEach(function(n){var p=D.landings[n];
  L.circleMarker([p[0],p[1]],{radius:3,weight:1,color:'#fbbf24',fillColor:'#b45309',fillOpacity:.8}).bindTooltip(n,{className:'lbl',permanent:map.getZoom()>=9,direction:'right'}).addTo(portLayer);});
 map.on('zoomend',function(){portLayer.eachLayer(function(l){var t=l.getTooltip();if(t){t.options.permanent=map.getZoom()>=9;l.unbindTooltip().bindTooltip(t.getContent(),t.options);}});drawStops();});
 var ws=document.getElementById('week');ws.max=D.weeks.length-1;ws.value=-1;ws.oninput=function(){setWeek(+ws.value);};
 document.getElementById('play').onclick=togglePlay;
 document.getElementById('metric').onchange=function(e){S.metric=e.target.value;draw();};
 document.getElementById('kind').onchange=function(e){S.kind=e.target.value;draw();};
 document.getElementById('nearshore').onchange=function(e){S.nearshore=e.target.checked;draw();};
 var sel=document.getElementById('boat');
 var ports2={};D.boats.forEach(function(b){if(b.port)(ports2[b.port]=ports2[b.port]||{})[b.landing]=1;});
 Object.keys(ports2).sort().forEach(function(p){var og=document.createElement('optgroup');og.label=p;
  var o=document.createElement('option');o.value='P:'+p;o.textContent='All '+p+' boats';og.appendChild(o);
  Object.keys(ports2[p]).sort().forEach(function(l){var o2=document.createElement('option');o2.value='L:'+l;o2.textContent='  '+l+' (landing)';og.appendChild(o2);
   D.boats.forEach(function(b,i){if(b.landing===l&&b.trips>0){var o3=document.createElement('option');o3.value=String(i);o3.textContent='    '+b.name;og.appendChild(o3);}});});
  sel.appendChild(og);});
 sel.onchange=function(e){setBoat(e.target.value);};
 setWeek(-1);
 window.__fleetMap=map;}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('panel').innerHTML='<b>Could not load fleet data.</b><br/><span class="mut">data.json failed to fetch.</span>';});
