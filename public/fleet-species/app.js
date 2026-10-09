'use strict';
/* Species Hotspots (dev): where the SoCal fleet caught each species, by time
   of year. Two kinds of evidence, never blended into one number:
   - AIS stops: a boat's posted dock count joined to its tracked trip; the
     count lands on the offshore stops AIS saw (hexagons, H3 res 6).
   - Report spots: the landings' written reports that name a charted spot and
     the species (purple circles at the spot).
   The calendar is every posted count, fleet-wide, AIS or not. */
var LAND_URL='/data/land.geojson';
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var SEASONS=[['All year',[1,2,3,4,5,6,7,8,9,10,11,12]],['Spring',[3,4,5]],['Summer',[6,7,8]],['Fall',[9,10,11]],['Winter',[12,1,2]]];
var D,map,hexLayer,spotLayer,pathLayer,CATS={},PATHS={};
var S={sp:'dorado',months:[1,2,3,4,5,6,7,8,9,10,11,12],year:'all',src:'both',metric:'count',cell:null,spot:null,trip:null};
var AGG={cells:{},spots:{}};

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmtDate(s){var d=new Date(s.slice(0,10)+'T12:00:00Z');return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});}
function n0(x){return Math.round(x).toLocaleString('en-US');}
function pct(x){return Math.round(x*100)+'%';}
var RAMP=[[30,41,59],[120,53,15],[217,119,6],[251,191,36],[254,240,138]];
function color(v){v=Math.max(0,Math.min(1,v));var p=v*(RAMP.length-1),i=Math.floor(p),f=p-i;var a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];
 return 'rgb('+Math.round(a[0]+(b[0]-a[0])*f)+','+Math.round(a[1]+(b[1]-a[1])*f)+','+Math.round(a[2]+(b[2]-a[2])*f)+')';}

/* ---------- selection ---------- */
function monthOn(key){var y=key.slice(0,4),m=+key.slice(5,7);if(S.year!=='all'&&y!==S.year)return false;return S.months.indexOf(m)>=0;}
function selLabel(){var m=S.months.slice().sort(function(a,b){return a-b;});
 var lbl=m.length===12?'all year':SEASONS.filter(function(s){return s[1].length===m.length&&s[1].every(function(x){return m.indexOf(x)>=0;});}).map(function(s){return s[0].toLowerCase();})[0]||m.map(function(x){return MON[x-1];}).join(', ');
 return lbl+(S.year==='all'?'':' '+S.year);}

function aggregate(){var cells={},spots={};
 var g=D.grid[S.sp]||{};
 Object.keys(D.effort).forEach(function(h){var e=D.effort[h],tot=0,tmin=0;Object.keys(e).forEach(function(mo){if(monthOn(mo)){tot+=e[mo][0];tmin+=e[mo][1];}});
  var pos=0,fish=0,min=0,gc=g[h]||{};Object.keys(gc).forEach(function(mo){if(monthOn(mo)){pos+=gc[mo][0];fish+=gc[mo][1];min+=gc[mo][2];}});
  if(tot>0||pos>0)cells[h]={pos:pos,fish:fish,min:min,tot:tot,tmin:tmin};});
 Object.keys(D.rspots).forEach(function(name){var e=D.rspots[name],tot=0,pos=0;
  Object.keys(e.tot).forEach(function(mo){if(monthOn(mo))tot+=e.tot[mo];});
  var sp=(e.sp[S.sp])||{};Object.keys(sp).forEach(function(mo){if(monthOn(mo))pos+=sp[mo];});
  if(tot>0)spots[name]={pos:pos,tot:tot,ll:e.ll,approx:e.approx};});
 AGG={cells:cells,spots:spots};return AGG;}

/* ---------- map ---------- */
function draw(){aggregate();hexLayer.clearLayers();spotLayer.clearLayers();
 var showAis=S.src!=='rep',showRep=S.src!=='ais';
 var cells=AGG.cells,keys=Object.keys(cells),vmax=1;
 if(showAis){var vals=keys.map(function(h){return S.metric==='rate'?(cells[h].tot?cells[h].pos/cells[h].tot:0):cells[h].pos;}).filter(function(v){return v>0;}).sort(function(a,b){return a-b;});
  vmax=vals.length?vals[Math.floor((vals.length-1)*0.97)]||vals[vals.length-1]:1;if(S.metric==='rate')vmax=Math.max(vmax,.05);
  keys.forEach(function(h){var c=cells[h],cg=D.cells[h];if(!cg)return;
   var v=S.metric==='rate'?(c.tot?c.pos/c.tot:0):c.pos;
   var few=S.metric==='rate'&&c.tot<3;
   var t=v>0?(S.metric==='rate'?v/vmax:Math.sqrt(v/vmax)):0;
   var poly=L.polygon(cg.b,{color:h===S.cell?'#fff':'#0b1220',weight:h===S.cell?2:.5,fillColor:v>0?color(.18+.82*t):'#1e293b',fillOpacity:v>0?(few?.35:.78):.25,interactive:true});
   poly.bindTooltip(cellTip(h,c),{className:'cell-tip',sticky:true,direction:'top'});
   poly.on('click',function(){S.cell=(S.cell===h)?null:h;S.spot=null;S.trip=null;commit();});
   poly.addTo(hexLayer);});}
 if(showRep){var sp=AGG.spots,smax=1;Object.keys(sp).forEach(function(n){if(S.metric==='rate'?false:sp[n].pos>smax)smax=sp[n].pos;});
  Object.keys(sp).forEach(function(n){var x=sp[n];if(!x.ll)return;var v=S.metric==='rate'?x.pos/x.tot:x.pos/smax;
   var r=x.pos>0?6+14*Math.sqrt(S.metric==='rate'?v:v):3.5;
   var m=L.circleMarker(x.ll,{radius:r,weight:n===S.spot?3:2,color:x.pos>0?'#a78bfa':'#475569',fillColor:'#7c3aed',fillOpacity:x.pos>0?.28:0,dashArray:x.approx?'3 3':null});
   m.bindTooltip(spotTip(n,x),{className:'cell-tip',direction:'top'});
   if(x.pos>0)m.bindTooltip(n,{permanent:true,direction:'right',offset:[r+2,0],className:'spot-lbl'});
   m.on('click',function(){S.spot=(S.spot===n)?null:n;S.cell=null;S.trip=null;commit();});
   m.addTo(spotLayer);});}
 document.getElementById('legend').innerHTML=legendHtml(vmax,showAis,showRep);
 renderPanel();}
function nearTxt(h){var c=D.cells[h];if(!c)return '';return c.near?'near '+esc(c.near[0])+(c.near[1]>=1?' ('+c.near[1].toFixed(0)+' nm)':''):c.c[0].toFixed(2)+', '+c.c[1].toFixed(2);}
function cellTip(h,c){return '<b>'+nearTxt(h)+'</b><br/>'+c.pos+' of '+c.tot+' counted trips that stopped here caught '+esc(CATS[S.sp].label.toLowerCase())
 +(c.pos?'<br/>~'+n0(c.fish)+' fish attributed (by stop time)':'')+'<br/><span class="mut">'+selLabel()+' · click for the trips</span>';}
function spotTip(n,x){return '<b>'+esc(n)+'</b>'+(x.approx?' <span class="mut">(approx.)</span>':'')+'<br/>'+x.pos+' of '+x.tot+' reports naming it mention '+esc(CATS[S.sp].label.toLowerCase())+'<br/><span class="mut">'+selLabel()+' · click for the reports</span>';}
function legendHtml(vmax,a,r){var h='<div><b style="color:#e2e8f0">'+esc(CATS[S.sp].label)+'</b> · '+selLabel()+'</div>';
 if(a){var vs=S.metric==='rate'?[.25,.5,.75,1].map(function(f){return f*vmax;}):[1,Math.round(vmax*.25),Math.round(vmax*.5),Math.round(vmax)].filter(function(v,i,arr){return v>=1&&arr.indexOf(v)===i;});
  h+='<div style="margin-top:4px">Hexagons (AIS stops): '+(S.metric==='rate'?'share of counted trips stopping there that caught it':'trips that caught it and stopped there')+'</div><div>'
  +vs.map(function(v){var t=S.metric==='rate'?v/vmax:Math.sqrt(v/vmax);return '<span class="sw" style="background:'+color(.18+.82*t)+'"></span>'+(S.metric==='rate'?pct(v):v)+'&nbsp;';}).join('')+'</div>'
  +(S.metric==='rate'?'<div>Faded = fewer than 3 counted trips</div>':'');}
 if(r)h+='<div class="rep" style="margin-top:4px"><span class="dot"></span>Report spots: bigger = more reports naming the species there; dashed = approximate position</div>';
 return h;}

/* ---------- panel ---------- */
function calendarSvg(){var cal=D.calendar[S.sp],W=356,H=86,P={l:24,r:4,t:6,b:16},n=cal.length;
 var bw=(W-P.l-P.r)/n,max=1;cal.forEach(function(c){if(c[0]>max)max=c[0];});
 var y=function(v){return P.t+(H-P.t-P.b)*(1-v/max);};
 var s='<svg class="cal" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Weekly posted trips that caught '+esc(CATS[S.sp].label)+'">';
 s+='<line class="gl" x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y(max)+'" y2="'+y(max)+'"/><text class="ax" x="'+(P.l-3)+'" y="'+(y(max)+3)+'" text-anchor="end">'+max+'</text>';
 s+='<line class="gl" x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y(0)+'" y2="'+y(0)+'"/><text class="ax" x="'+(P.l-3)+'" y="'+(y(0)+3)+'" text-anchor="end">0</text>';
 var lastMo=-1,lastX=-99,lastW=0;
 cal.forEach(function(c,i){var wk=D.weeks[i],mo=+wk.slice(5,7),x=P.l+i*bw;
  if(mo!==lastMo){var lbl=MON[mo-1]+(mo===1||lastMo<0?' ’'+wk.slice(2,4):'');
   if((mo%3===1||lastMo<0)&&x-lastX>=lastW+6){s+='<text class="ax" x="'+x+'" y="'+(H-4)+'">'+lbl+'</text>';lastX=x;lastW=lbl.length*5;}lastMo=mo;}
  var on=monthOn(wk)&&!(S.year!=='all'&&wk.slice(0,4)!==S.year);
  var h=c[0]?Math.max(2,y(0)-y(c[0])):0;
  s+='<rect data-i="'+i+'" x="'+(x+.5).toFixed(2)+'" y="'+(y(0)-h).toFixed(2)+'" width="'+Math.max(1,bw-1).toFixed(2)+'" height="'+h.toFixed(2)+'" rx="1" fill="'+(on?'#fbbf24':'#475569')+'"/>';
  s+='<rect data-i="'+i+'" x="'+x.toFixed(2)+'" y="'+P.t+'" width="'+bw.toFixed(2)+'" height="'+(H-P.t-P.b)+'" fill="transparent" class="hit"/>';});
 return s+'</svg>';}
function wireCalendar(){var tip=document.getElementById('tip');
 document.querySelectorAll('.cal .hit').forEach(function(r){r.addEventListener('mousemove',function(e){var i=+r.getAttribute('data-i'),c=D.calendar[S.sp][i];
  tip.innerHTML='Week of '+fmtDate(D.weeks[i])+'<br/><b>'+c[0]+'</b> of '+D.week_total[i]+' posted trips caught '+esc(CATS[S.sp].label.toLowerCase())+(c[1]?' · '+n0(c[1])+' fish':'');
  tip.style.display='block';tip.style.left=Math.min(window.innerWidth-240,e.clientX+12)+'px';tip.style.top=(e.clientY-40)+'px';});
  r.addEventListener('mouseleave',function(){tip.style.display='none';});});}

function selCounts(){var pos=0,fish=0,tot=0;D.weeks.forEach(function(w,i){if(!monthOn(w))return;pos+=D.calendar[S.sp][i][0];fish+=D.calendar[S.sp][i][1];tot+=D.week_total[i];});
 var loc=0,unl=0;D.trips.forEach(function(t){if(t.sp[S.sp]&&monthOn(t.d.slice(0,7)))loc++;});
 var u=D.unlocated[S.sp]||{};Object.keys(u).forEach(function(mo){if(monthOn(mo))unl+=u[mo];});
 var rep=0;D.reports.forEach(function(r){if(r.sp[S.sp]!=null&&monthOn(r.d.slice(0,7)))rep++;});
 return {pos:pos,fish:fish,tot:tot,loc:loc,unl:unl,rep:rep};}

function topCells(){var c=AGG.cells;return Object.keys(c).filter(function(h){return c[h].pos>0&&(S.metric!=='rate'||c[h].tot>=3);})
 .sort(function(a,b){return S.metric==='rate'?(c[b].pos/c[b].tot-c[a].pos/c[a].tot)||(c[b].pos-c[a].pos):(c[b].pos-c[a].pos)||(c[b].fish-c[a].fish);}).slice(0,8);}
function topSpots(){var s=AGG.spots;return Object.keys(s).filter(function(n){return s[n].pos>0;}).sort(function(a,b){return s[b].pos-s[a].pos;}).slice(0,8);}

function renderPanel(){var p=document.getElementById('panel');var html;
 if(S.cell&&AGG.cells[S.cell])html=cellPanel(S.cell);else if(S.spot&&AGG.spots[S.spot])html=spotPanel(S.spot);else html=overview();
 p.innerHTML=html;wireCalendar();
 p.querySelectorAll('[data-cell]').forEach(function(el){el.onclick=function(){S.cell=el.getAttribute('data-cell');S.spot=null;S.trip=null;commit();var c=D.cells[S.cell];if(c)map.panTo(c.c);};});
 p.querySelectorAll('[data-spot]').forEach(function(el){el.onclick=function(){S.spot=el.getAttribute('data-spot');S.cell=null;S.trip=null;commit();var s=D.rspots[S.spot];if(s)map.panTo(s.ll);};});
 p.querySelectorAll('[data-trip]').forEach(function(el){el.onclick=function(){var k=el.getAttribute('data-trip').split('.');S.trip=[+k[0],+k[1]];drawTrip();renderPanel();};});
 p.querySelectorAll('[data-back]').forEach(function(el){el.onclick=function(){S.cell=null;S.spot=null;S.trip=null;pathLayer.clearLayers();commit();};});}

function overview(){var k=CATS[S.sp],c=selCounts();
 var h='<h3>'+esc(k.label)+'</h3><div class="mut">'+esc(selLabel())+' · dock counts '+fmtDate(D.meta.counts_window[0])+' – '+fmtDate(D.meta.counts_window[1])+'</div>'
  +'<div class="stats"><div class="stat"><b>'+n0(c.pos)+'</b><span>posted trips that caught it (all boats)</span></div>'
  +'<div class="stat"><b>'+n0(c.fish)+'</b><span>fish on those counts</span></div>'
  +'<div class="stat"><b>'+n0(c.loc)+'</b><span>of them placed by AIS stops'+(c.unl?' · '+c.unl+' tracked but not seen stopping':'')+'</span></div>'
  +'<div class="stat"><b>'+n0(c.rep)+'</b><span>landing reports mention it</span></div></div>'
  +'<h4>When: posted trips that caught it, by week</h4>'+calendarSvg()
  +'<div class="mut" style="font-size:11px">Amber = the months selected. Every boat that posts a count, on AIS or not.</div>';
 var tc=topCells(),ts=topSpots();
 h+='<h4>Where AIS saw those boats fish</h4>';
 if(!tc.length)h+='<div class="empty">No AIS-placed trips for this selection.'+(c.pos?' The posted trips fall where AIS has no tracks (see below).':'')+'</div>';
 else{h+='<table><tr><th>Area</th><th class="n">Caught it</th><th class="n">Of trips</th><th class="n">Fish</th></tr>';
  tc.forEach(function(x){var a=AGG.cells[x];h+='<tr class="row" data-cell="'+x+'"><td>'+nearTxt(x)+'</td><td class="n">'+a.pos+'</td><td class="n">'+a.tot+'</td><td class="n">~'+n0(a.fish)+'</td></tr>';});h+='</table>';}
 h+='<h4>Spots the landing reports name</h4>';
 if(!ts.length)h+='<div class="empty">No report names a charted spot with this species for this selection.</div>';
 else{h+='<table><tr><th>Spot</th><th class="n">Reports</th><th class="n">Of reports there</th></tr>';
  ts.forEach(function(n){var a=AGG.spots[n];h+='<tr class="row" data-spot="'+esc(n)+'"><td>'+esc(n)+(a.approx?' <span class="mut">≈</span>':'')+'</td><td class="n">'+a.pos+'</td><td class="n">'+a.tot+'</td></tr>';});h+='</table>';}
 h+='<div class="caveat"><b>How to read this.</b> Hexagons come from AIS: a boat\'s dock count joined to its tracked trip (same port, boat, return date and a trip length that fits the posted trip type), with the catch spread over the offshore stops AIS saw. AIS here is shore receivers only and the public archive ends '+fmtDate(D.meta.ais_window[1])+', so trips past ~50 nm, in Mexican waters, or after that date are not placed by it. Purple circles come from the landings\' written reports: a report that names both the species and a charted spot. A report naming several spots counts at each. Neither layer is a fish density; they show where the fleet went on the days it caught the species. Dorado and mahi-mahi are the same fish.</div>';
 return h;}

function cellPanel(hx){var a=AGG.cells[hx],c=D.cells[hx];
 var trips=D.trips.filter(function(t){return t.sp[S.sp]&&monthOn(t.d.slice(0,7))&&t.cells.indexOf(hx)>=0;}).sort(function(x,y){return x.d<y.d?1:-1;});
 var h='<div class="navrow"><button class="chip" data-back="1">‹ '+esc(CATS[S.sp].label)+'</button></div><h3>'+nearTxt(hx)+'</h3>'
  +'<div class="mut">'+c.c[0].toFixed(3)+', '+c.c[1].toFixed(3)+' · '+esc(selLabel())+'</div>'
  +'<div class="stats"><div class="stat"><b>'+a.pos+'</b><span>trips that caught it stopped here</span></div><div class="stat"><b>'+a.tot+'</b><span>counted trips stopped here</span></div></div>'
  +'<h4>The trips <span class="mut">· tap one to draw its path</span></h4><table><tr><th>Left</th><th>Boat</th><th>Trip</th><th class="n">'+esc(CATS[S.sp].label.split(' ')[0])+'</th><th class="n">Anglers</th></tr>';
 trips.forEach(function(t){var sel=S.trip&&S.trip[0]===t.m&&S.trip[1]===t.id;
  h+='<tr class="row'+(sel?' sel':'')+'" data-trip="'+t.m+'.'+t.id+'"><td>'+fmtDate(t.d)+'</td><td>'+esc(t.b)+'<br/><span class="mut">'+esc(t.l)+'</span></td><td>'+esc(t.tt)+'<br/><span class="mut">'+t.h+' h · '+t.nm+' nm</span></td><td class="n">'+t.sp[S.sp]+'</td><td class="n">'+t.a+'</td></tr>';});
 return h+'</table><div class="caveat">The count is the landing\'s posted total for the trip; a trip that stopped in several cells is listed in each.</div>';}

function spotPanel(n){var a=AGG.spots[n];
 var reps=D.reports.filter(function(r){return r.sp[S.sp]!=null&&monthOn(r.d.slice(0,7))&&r.s.indexOf(n)>=0;}).sort(function(x,y){return x.d<y.d?1:-1;});
 var h='<div class="navrow"><button class="chip" data-back="1">‹ '+esc(CATS[S.sp].label)+'</button></div><h3>'+esc(n)+'</h3>'
  +'<div class="mut">'+(a.approx?'approximate position · ':'chart position · ')+esc(selLabel())+'</div>'
  +'<div class="stats"><div class="stat"><b>'+a.pos+'</b><span>reports naming it mention the species</span></div><div class="stat"><b>'+a.tot+'</b><span>reports name this spot</span></div></div>'
  +'<h4>The reports</h4><table><tr><th>Date</th><th>Report</th></tr>';
 reps.forEach(function(r){h+='<tr><td>'+fmtDate(r.d)+'</td><td><a href="'+esc(r.u)+'" target="_blank" rel="noopener">'+esc(r.t)+'</a><br/><span class="mut">'+esc(r.a)+(r.l&&r.l!==r.a?' · '+esc(r.l):'')+(r.sp[S.sp]?' · '+r.sp[S.sp]+' quoted':'')+(r.s.length>1?' · also names '+esc(r.s.filter(function(x){return x!==n;}).join(', ')):'')+'</span></td></tr>';});
 return h+'</table>';}

/* ---------- one trip's path, from Fleet Tracks' per-boat trip files ---------- */
function drawTrip(){pathLayer.clearLayers();if(!S.trip)return;var m=S.trip[0],id=S.trip[1];
 var go=function(j){var t=(j&&j.trips||[]).filter(function(x){return x.id===id;})[0];if(!t||t.pts.length<2)return;
  var ll=t.pts.map(function(p){return [p[1],p[2]];});
  L.polyline(ll,{color:'#0b1220',weight:6,opacity:.6,interactive:false}).addTo(pathLayer);
  L.polyline(ll,{color:'#4ade80',weight:3,opacity:.95,interactive:false}).addTo(pathLayer);
  L.circleMarker(ll[0],{radius:5,color:'#0b1220',weight:2,fillColor:'#4ade80',fillOpacity:1}).addTo(pathLayer);
  map.fitBounds(ll,{padding:[30,30],maxZoom:10,paddingBottomRight:[window.innerWidth>760?400:0,window.innerWidth>760?0:Math.round(window.innerHeight*.48)]});};
 if(PATHS[m])return go(PATHS[m]);
 fetch('/fleet/trips/'+m+'.json',{cache:'no-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(j){PATHS[m]=j;go(j);}).catch(function(){});}

/* ---------- URL state + controls ---------- */
function stateToHash(){var p=['sp='+S.sp];if(S.months.length<12)p.push('m='+S.months.join('.'));if(S.year!=='all')p.push('y='+S.year);
 if(S.src!=='both')p.push('src='+S.src);if(S.metric!=='count')p.push('by='+S.metric);if(S.cell)p.push('cell='+S.cell);if(S.spot)p.push('spot='+encodeURIComponent(S.spot));return '#'+p.join('&');}
function readHash(){var q={};location.hash.replace(/^#/,'').split('&').forEach(function(kv){if(!kv)return;var i=kv.indexOf('=');q[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1));});
 S.sp=CATS[q.sp]?q.sp:'dorado';S.months=q.m?q.m.split('.').map(Number).filter(function(x){return x>=1&&x<=12;}):[1,2,3,4,5,6,7,8,9,10,11,12];if(!S.months.length)S.months=[1,2,3,4,5,6,7,8,9,10,11,12];
 S.year=q.y||'all';S.src=/^(both|ais|rep)$/.test(q.src||'')?q.src:'both';S.metric=q.by==='rate'?'rate':'count';
 S.cell=q.cell&&D.cells[q.cell]?q.cell:null;S.spot=q.spot&&D.rspots[q.spot]?q.spot:null;S.trip=null;}
function commit(){var h=stateToHash();if(h!==location.hash)history.pushState(null,'',location.pathname+h);sync();draw();}
function sync(){document.querySelectorAll('#species .chip').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-k')===S.sp);});
 document.querySelectorAll('#months .chip').forEach(function(b){b.classList.toggle('on',S.months.indexOf(+b.getAttribute('data-m'))>=0);});
 document.querySelectorAll('#seasons .chip').forEach(function(b){var m=JSON.parse(b.getAttribute('data-ms'));b.classList.toggle('on',m.length===S.months.length&&m.every(function(x){return S.months.indexOf(x)>=0;}));});
 document.getElementById('year').value=S.year;document.getElementById('src').value=S.src;document.getElementById('metric').value=S.metric;}

function boot(d){D=d;D.cats.forEach(function(c){CATS[c.key]=c;});
 map=L.map('map',{zoomControl:true,attributionControl:false});
 map.fitBounds([[31.6,-120.2],[34.2,-117.0]],{paddingTopLeft:[0,96],paddingBottomRight:[window.innerWidth>760?400:0,0]});
 var land=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(land);}).catch(function(){});
 hexLayer=L.layerGroup().addTo(map);spotLayer=L.layerGroup().addTo(map);pathLayer=L.layerGroup().addTo(map);
 var sp=document.getElementById('species');D.cats.forEach(function(c){var b=document.createElement('button');b.className='chip';b.setAttribute('data-k',c.key);
  b.textContent=c.label+' · '+n0(D.summary[c.key].posted_trips);b.title=n0(D.summary[c.key].posted_trips)+' posted trips caught it';b.onclick=function(){S.sp=c.key;S.cell=null;S.spot=null;S.trip=null;pathLayer.clearLayers();commit();};sp.appendChild(b);});
 var ms=document.getElementById('months');MON.forEach(function(m,i){var b=document.createElement('button');b.className='chip';b.setAttribute('data-m',i+1);b.textContent=m.slice(0,1)+m.slice(1,3);
  b.onclick=function(){var k=S.months.indexOf(i+1);if(S.months.length===12){S.months=[i+1];}else if(k>=0){S.months.splice(k,1);if(!S.months.length)S.months=[1,2,3,4,5,6,7,8,9,10,11,12];}else S.months.push(i+1);commit();};ms.appendChild(b);});
 var ss=document.getElementById('seasons');SEASONS.forEach(function(s){var b=document.createElement('button');b.className='chip';b.setAttribute('data-ms',JSON.stringify(s[1]));b.textContent=s[0];b.onclick=function(){S.months=s[1].slice();commit();};ss.appendChild(b);});
 var yr=document.getElementById('year');var years={};D.months.forEach(function(m){years[m.slice(0,4)]=1;});
 [['all','Both years']].concat(Object.keys(years).sort().map(function(y){return [y,y];})).forEach(function(o){var e=document.createElement('option');e.value=o[0];e.textContent=o[1];yr.appendChild(e);});
 yr.onchange=function(){S.year=yr.value;commit();};
 document.getElementById('src').onchange=function(e){S.src=e.target.value;commit();};
 document.getElementById('metric').onchange=function(e){S.metric=e.target.value;commit();};
 window.addEventListener('popstate',function(){readHash();sync();draw();});
 // the control header wraps to a different height per screen: park the map
 // controls and the panel just under it
 var fit=function(){var h=document.getElementById('bar').offsetHeight,p=document.getElementById('panel'),tl=document.querySelector('.leaflet-top.leaflet-left');
  if(tl)tl.style.top=(h+8)+'px';p.style.top=window.innerWidth>760?(h+10)+'px':'';p.style.maxHeight=window.innerWidth>760?'calc(100% - '+(h+26)+'px)':'';};
 window.addEventListener('resize',fit);fit();
 readHash();sync();draw();window.__speciesMap=map;}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){document.getElementById('panel').innerHTML='<b>Could not load data.json.</b>';});
