'use strict';
/* Fleet Tracks: where the SoCal sportfishing fleet actually stops, from public
   AIS. Everything aggregates client-side from data.json's stop list, so the
   boat / week / stop-kind filters are instant. CSP-clean: same-origin only.

   Navigation model: the view state S (boat, week, metric, kind, nearshore,
   cell, trip) is mirrored into the URL hash on every change, and restored
   from it on load and on popstate, so the browser's back/forward buttons walk
   through boat -> trip -> cell selections and links are shareable. */
var LAND_URL='/data/land.geojson';
var CONV_BOATS=3;          // "fleet piled in" = this many boats in one cell on one day
/* US / Mexico maritime boundary, Pacific: land terminus -> 12 nm point (1970
   treaty), then OP-1..OP-4 of the 1978 Treaty on Maritime Boundaries (UN
   DOALOS text). Degrees from the treaty's D-M-S. */
var MX_BORDER=[[32.5344,-117.1249],[32.58948,-117.46373],[32.62694,-117.82528],[31.13278,-118.60500],[30.54200,-121.86621]];
var D,map,hexLayer,stopLayer,portLayer,borderLayer,wpMarker,coordbox,STOPWEEK=[],WEEKIDX={},MMSI_IDX={};
var S={week:-1,metric:'fish',boat:'all',kind:'all',nearshore:false,cell:null,trip:null,playing:null,pick:false};
var LAST_AGG={};

function fmtDate(s){var d=new Date(s+'T12:00:00Z');return d.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});}
function hrs(m){return m<60?Math.round(m)+' min':(m/60).toFixed(m<600?1:0)+' h';}
function pct(x){return x==null?'—':Math.round(x*100)+'%';}
function nm(km){return Math.round(km/1.852)+' nm';}
function short(l){return String(l||'').replace(/ Sportfishing| Landing| Sea Center/g,'').replace("Davey's Locker","Davey's");}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function isSingleBoat(){return S.boat!=='all'&&S.boat.slice(0,2)!=='L:'&&S.boat.slice(0,2)!=='P:';}
function boatIdx(){return isSingleBoat()?+S.boat:-1;}
function _ddm(v,pos,neg){var h=v>=0?pos:neg;v=Math.abs(v);var d=Math.floor(v);return d+'°'+((v-d)*60).toFixed(3)+"' "+h;}
function _fmt(ll){return {dd:ll.lat.toFixed(5)+', '+ll.lng.toFixed(5),dm:_ddm(ll.lat,'N','S')+'  '+_ddm(ll.lng,'E','W')};}
function toast(msg){var t=document.getElementById('toast');if(!t){t=document.createElement('div');t.id='toast';t.className='toast';document.body.appendChild(t);}
 t.textContent=msg;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(function(){t.style.display='none';},2500);}

/* sequential ramp (dark slate -> amber -> pale yellow), value in 0..1 */
var RAMP=[[30,41,59],[120,53,15],[217,119,6],[251,191,36],[254,240,138]];
function color(v){v=Math.max(0,Math.min(1,v));var p=v*(RAMP.length-1),i=Math.floor(p),f=p-i;var a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];
 return 'rgb('+Math.round(a[0]+(b[0]-a[0])*f)+','+Math.round(a[1]+(b[1]-a[1])*f)+','+Math.round(a[2]+(b[2]-a[2])*f)+')';}

/* ---------- URL state ---------- */
function stateToHash(){var p=[];
 if(S.boat!=='all')p.push('boat='+encodeURIComponent(isSingleBoat()?D.boats[+S.boat].mmsi:S.boat));
 if(S.week>=0)p.push('week='+D.weeks[S.week]);
 if(S.metric!=='fish')p.push('color='+S.metric);
 if(S.kind!=='all')p.push('stops='+S.kind);
 if(S.nearshore)p.push('neardock=1');
 if(S.cell)p.push('cell='+S.cell);
 if(S.trip)p.push('trip='+D.boats[S.trip[0]].mmsi+'.'+S.trip[1]);
 return p.length?'#'+p.join('&'):'';}
function readHash(){var h=location.hash.replace(/^#/,''),q={};
 h.split('&').forEach(function(kv){if(!kv)return;var i=kv.indexOf('=');q[decodeURIComponent(kv.slice(0,i))]=decodeURIComponent(kv.slice(i+1));});
 S.boat='all';
 if(q.boat){if(q.boat.slice(0,2)==='L:'||q.boat.slice(0,2)==='P:')S.boat=q.boat;else if(MMSI_IDX[q.boat]!=null)S.boat=String(MMSI_IDX[q.boat]);}
 S.week=(q.week&&WEEKIDX[q.week]!=null)?WEEKIDX[q.week]:-1;
 S.metric=/^(fish|boats|trips|conv|cpa)$/.test(q.color||'')?q.color:'fish';
 S.kind=(q.stops==='0'||q.stops==='1')?q.stops:'all';
 S.nearshore=q.neardock==='1';
 S.cell=(q.cell&&D.cells[q.cell])?q.cell:null;
 S.trip=null;
 if(q.trip){var t=q.trip.split('.');if(MMSI_IDX[t[0]]!=null&&isSingleBoat()&&+S.boat===MMSI_IDX[t[0]])S.trip=[MMSI_IDX[t[0]],+t[1]];}}
function commit(){var h=stateToHash();if(h!==location.hash&&!(h===''&&location.hash==='')){history.pushState(null,'',location.pathname+location.search+h);}
 syncControls();draw();}
function syncControls(){
 document.getElementById('boat').value=S.boat;
 document.getElementById('metric').value=S.metric;
 document.getElementById('kind').value=S.kind;
 document.getElementById('nearshore').checked=S.nearshore;
 var ws=document.getElementById('week');ws.value=S.week;
 document.getElementById('wall').classList.toggle('on',S.week<0);
 document.getElementById('wlabel').textContent=S.week<0?'Whole period ('+fmtDate(D.meta.start)+' – '+fmtDate(D.meta.end)+')':'Week of '+fmtDate(D.weeks[S.week])+' '+D.weeks[S.week].slice(0,4);}

/* ---------- filtering + aggregation ---------- */
function boatMatch(bi){if(S.boat==='all')return true;
 if(S.boat.slice(0,2)==='L:')return D.boats[bi].landing===S.boat.slice(2);
 if(S.boat.slice(0,2)==='P:')return D.boats[bi].port===S.boat.slice(2);
 return bi===+S.boat;}
function stopVisible(s,i){
 if(!boatMatch(s[0]))return false;
 if(S.kind!=='all'&&s[5]!==+S.kind)return false;
 if(!S.nearshore&&s[8])return false;
 if(S.week>=0&&STOPWEEK[i]!==S.week)return false;
 return true;}
function aggregate(){var A={};
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];if(!stopVisible(s,i))continue;
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
function metricLabel(){return {fish:'time spent stopped and fishing',boats:'number of different boats',trips:'number of trips',conv:'days with '+CONV_BOATS+'+ boats in the cell',cpa:'fish kept per angler (trips with a dock count)'}[S.metric];}

/* ---------- drawing ---------- */
function draw(){
 var A=aggregate();LAST_AGG=A;hexLayer.clearLayers();
 var vals=[];Object.keys(A).forEach(function(h){var v=metricOf(A[h],h);if(v>0)vals.push(v);});
 vals.sort(function(a,b){return a-b;});
 var vmax=vals.length?vals[Math.floor((vals.length-1)*0.97)]:1;  // clip the top 3% so one mega-cell doesn't flatten the ramp
 if(vmax<=0)vmax=1;
 var n=0;
 Object.keys(A).forEach(function(h){var c=A[h],v=metricOf(c,h);if(v<=0||!D.cells[h])return;n++;
  var t=S.metric==='fish'?Math.sqrt(v/vmax):v/vmax;
  var poly=L.polygon(D.cells[h].b,{color:h===S.cell?'#fff':'#0b1220',weight:h===S.cell?2:.5,fillColor:color(t),fillOpacity:.72,interactive:true});
  poly.bindTooltip(tipHtml(h,c),{className:'cell-tip',sticky:true,direction:'top',offset:[0,-6]});
  poly.on('click',function(e){if(S.pick){dropWp(e.latlng);return;}S.cell=(S.cell===h)?null:h;commit();});
  poly.on('contextmenu',function(e){dropWp(e.latlng);});
  poly.addTo(hexLayer);});
 drawStops();
 document.getElementById('legend').innerHTML=legendHtml(vmax,n);placeReadout();
 if(S.cell&&!A[S.cell])S.cell=null;
 renderPanel(A,n);}

function tipHtml(h,c){var top=Object.keys(c.byBoat).map(function(b){return [b,c.byBoat[b]];}).sort(function(a,b){return b[1]-a[1];}).slice(0,4);
 var k=D.cell_catch[h];
 return '<b>'+hrs(c.fish)+'</b> fishing here · '+c.nBoats+' boat'+(c.nBoats>1?'s':'')+' · '+c.nTrips+' trip'+(c.nTrips>1?'s':'')+' · '+c.nDays+' day'+(c.nDays>1?'s':'')
  +(c.conv?'<br/><span style="color:#fbbf24">'+c.conv+' day'+(c.conv>1?'s':'')+' with '+CONV_BOATS+'+ boats here</span>':'')
  +(c.troll?'<br/>'+Math.round(100*c.troll/c.fish)+'% of it trolling':'')
  +(k?'<br/>'+k[0]+' fish/angler on '+k[1]+' counted trip'+(k[1]>1?'s':''):'')
  +'<br/><span class="mut">'+top.map(function(t){return esc(D.boats[t[0]].name)+' '+hrs(t[1]);}).join(' · ')+'</span>'
  +'<br/><span class="mut">'+(S.pick?'click to drop a GPS waypoint':'click for who and when')+'</span>';}

function legendHtml(vmax,n){var steps=[0,.25,.5,.75,1];var f=S.metric==='fish'?function(t){return hrs(t*t*vmax);}:function(t){return (Math.round(t*vmax*10)/10)+'';};
 var showing=isSingleBoat()?D.boats[boatIdx()].name:(S.boat==='all'?'whole fleet':S.boat.slice(2));
 var html='<div><b>Hexagons</b> = '+metricLabel()+'<br/>'+esc(showing)+' · '+(S.week>=0?'week of '+fmtDate(D.weeks[S.week]):'whole period')+(S.kind==='0'?' · drifting only':S.kind==='1'?' · trolling only':'')+'</div>'
  +'<div style="margin:4px 0">'+steps.map(function(t){return '<span class="sw" style="background:'+color(t)+'"></span>'+f(t)+'&nbsp;&nbsp;';}).join('')+'</div>';
 if(!n)html+='<div>Nothing to show for this selection.</div>';
 else if(map.getZoom()<10&&!isSingleBoat())html+='<div>Each cell ≈ 1.4 km across. Zoom in to see individual stops.</div>';
 else html+='<div>Dots are individual stops: white = drifting/anchored, blue = trolling. Bigger = longer.</div>';
 html+='<div><span class="ln"></span>US / Mexico maritime boundary · AIS coverage ends well before it</div>';
 return html;}

function drawStops(){stopLayer.clearLayers();
 var showAll=map.getZoom()>=10,single=isSingleBoat();
 if(!showAll&&!single)return;
 var bounds=[];
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];if(!stopVisible(s,i))continue;
  if(S.trip!=null&&!(s[0]===S.trip[0]&&s[7]===S.trip[1]))continue;
  var r=Math.max(2,Math.min(9,Math.sqrt(s[4]/8)));
  if(S.trip)bounds.push([s[2],s[3]]);
  L.circleMarker([s[2],s[3]],{radius:r,weight:s[5]===1?1.5:0.5,color:s[5]===1?'#38bdf8':'#fff',fillColor:s[5]===1?'#0ea5e9':'#f8fafc',fillOpacity:.55})
   .bindTooltip(esc(D.boats[s[0]].name)+' · '+fmtDate(s[1])+' · '+hrs(s[4])+(s[5]===1?' trolling':' drifting / anchored'),{className:'cell-tip',direction:'top'})
   .addTo(stopLayer);}
 if(S.trip&&bounds.length&&S._fitTrip){S._fitTrip=false;map.fitBounds(bounds,{padding:[40,40],maxZoom:11,paddingBottomRight:[window.innerWidth>760?380:0,0]});}}

/* ---------- GPS waypoint ---------- */
function placeReadout(){if(!coordbox)return;var lg=document.getElementById('legend');coordbox.style.bottom=(lg.offsetHeight+18)+'px';}
function setReadout(ll){if(!coordbox)return;var f=_fmt(ll);coordbox.innerHTML='<b>'+f.dd+'</b><br/>'+f.dm
 +'<div class="hint">'+(S.pick?'click to drop a waypoint':'right-click to drop a GPS waypoint')+'</div>';}
function openWp(ll){var f=_fmt(ll);
 wpMarker.setPopupContent('<div class="wpc"><b>'+f.dd+'</b><br/>'+f.dm+'</div>'
  +'<button class="copybtn" data-c="'+f.dd+'">Copy decimal</button><button class="copybtn" data-c="'+f.dm+'">Copy deg min</button><button class="copybtn" data-rm="1">Remove</button>');
 wpMarker.openPopup();}
function dropWp(ll){
 if(!wpMarker){wpMarker=L.marker(ll,{draggable:true,zIndexOffset:1100,icon:L.divIcon({className:'',html:'<div class=wp>⌖</div>',iconSize:[24,24],iconAnchor:[12,12]})}).addTo(map);
  wpMarker.bindPopup('',{closeButton:false});wpMarker.on('drag',function(){openWp(wpMarker.getLatLng());});wpMarker.on('click',function(){openWp(wpMarker.getLatLng());});}
 else wpMarker.setLatLng(ll);
 openWp(ll);}
function removeWp(){if(wpMarker){map.removeLayer(wpMarker);wpMarker=null;}}
function wireCopy(e){var el=e.popup.getElement();if(!el)return;
 el.querySelectorAll('.copybtn').forEach(function(b){b.onclick=function(){
  if(b.getAttribute('data-rm')){removeWp();return;}
  var txt=b.getAttribute('data-c');
  var done=function(){toast('Copied '+txt);};
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(done,function(){toast(txt);});
  else toast(txt);};});}
function togglePick(){S.pick=!S.pick;document.getElementById('gps').classList.toggle('on',S.pick);map.getContainer().classList.toggle('pick',S.pick);setReadout(map.getCenter());}

/* ---------- panel ---------- */
function renderPanel(A,n){var p=document.getElementById('panel');
 if(!p._wired){p._wired=true;p.addEventListener('click',onPanelClick);}
 var html;
 if(S.cell&&A[S.cell])html=cellPanel(S.cell,A[S.cell]);
 else if(isSingleBoat())html=boatPanel(boatIdx(),n);
 else html=fleetPanel(n);
 p.innerHTML=html;}

function onPanelClick(e){var p=document.getElementById('panel');
 var el=e.target.closest('[data-act]');
 if(el){var act=el.getAttribute('data-act');
  if(act==='fleet'){S.boat='all';S.cell=null;S.trip=null;}
  else if(act==='boat'){S.boat=el.getAttribute('data-b');S.cell=null;S.trip=null;}
  else if(act==='uncell'){S.cell=null;}
  else if(act==='trip'){var tid=+el.getAttribute('data-t'),bi=boatIdx();S.trip=(S.trip&&S.trip[1]===tid)?null:[bi,tid];S._fitTrip=!!S.trip;}
  else if(act==='alltrips'){S.trip=null;}
  else if(act==='week'){S.week=+el.getAttribute('data-w');}
  commit();return;}
 if(e.target.closest('h3')&&window.innerWidth<=760)p.classList.toggle('min');}

function howTo(){var seen=false;try{seen=localStorage.getItem('sd:fleet:seen')==='1';localStorage.setItem('sd:fleet:seen','1');}catch{seen=false;}
 return '<details class="how"'+(seen?'':' open')+'><summary>How to read this map</summary><ul>'
  +'<li>Each hexagon is about 1.4 km across. The brighter it is, the more time the fleet spent <b>stopped and fishing</b> there, from each boat\'s public AIS track.</li>'
  +'<li>Pick a boat in the table below (or the Boat menu) to see only its stops and its trips. Click a trip to isolate it on the map.</li>'
  +'<li>Use <b>‹ ›</b> or <b>Play</b> to step through the season week by week; <b>All weeks</b> brings back the whole period.</li>'
  +'<li>Click any hexagon for who fished it and when. Zoom in to see individual stops.</li>'
  +'<li><b>⌖ GPS</b> (or a right-click anywhere) drops a waypoint you can copy coordinates from.</li>'
  +'<li>The browser Back button undoes a selection.</li></ul></details>';}

function fleetPanel(n){var m=D.meta,s=D.summary;
 var rows=D.boats.map(function(b,i){return [i,b];}).filter(function(x){return boatMatch(x[0])&&x[1].trips>0;})
  .sort(function(a,b){return b[1].fish_min-a[1].fish_min;});
 var scope=S.boat==='all'?'The SoCal sportfishing fleet':S.boat.slice(2)+' boats';
 var html='';
 if(S.boat!=='all')html+='<div class="navrow"><button class="chip" data-act="fleet">‹ Whole fleet</button></div>';
 html+='<h3>'+esc(scope)+', from AIS</h3>'
  +'<div class="mut">'+m.n_boats+' boats · '+m.n_trips+' trips · '+m.n_stops+' fishing stops · '+fmtDate(m.start)+' – '+fmtDate(m.end)+' '+m.end.slice(0,4)+'</div>'
  +howTo();
 if(!n)html+='<div class="empty">No stops match this selection. Try <button class="chip" data-act="week" data-w="-1">all weeks</button> or another boat.</div>';
 if(S.boat==='all')html+='<h4>Three questions, whole fleet</h4>'
  +'<div class="ans"><div class="q">Does a boat go back to the same spot next trip?</div><div class="a">The median boat puts <b>'+pct(s.return_same_spot_median)+'</b> of its next trip\'s fishing time in the same 0.5 km cells and <b>'+pct(s.return_same_area_median)+'</b> within about 1.5 km of them; the fleet average is '+pct(s.return_same_area_mean)+' ('+(s.trip_pairs||0)+' trip pairs).</div></div>'
  +'<div class="ans"><div class="q">Does it work a home zone?</div><div class="a">The median boat spends <b>'+pct(s.home_top_share_median)+'</b> of its season in one '+(m.h3_res===7?'1.4':'0.5')+' km cell and needs <b>'+(s.home_cells_for_half_median||'—')+'</b> cells to cover half its fishing time.</div></div>'
  +'<div class="ans"><div class="q">Does the fleet pile onto a bite?</div><div class="a"><b>'+(s.convergence_days_total||0)+'</b> boat-days had '+CONV_BOATS+' or more fleet boats in the same cell; the median boat spends <b>'+pct(s.convergence_share_median)+'</b> of its fishing days in one of those.</div></div>';
 html+='<h4>Boats <span class="mut">· click one</span></h4><table class="boats"><tr><th>Boat</th><th class="n">Trips</th><th class="n">Fishing</th><th class="n" title="share of the next trip within 1.5 km of the previous trip">Same area</th><th class="n" title="share of the season in the top cell">Home</th><th class="n" title="share of trip time with an AIS position">AIS</th></tr>';
 rows.forEach(function(x){var b=x[1];html+='<tr class="row" data-act="boat" data-b="'+x[0]+'"><td>'+esc(b.name)+'<br/><span class="mut">'+esc(short(b.landing))+'</span></td><td class="n">'+b.trips+'</td><td class="n">'+hrs(b.fish_min)+'</td><td class="n">'+pct(b.return_same_area)+'</td><td class="n">'+pct(b.top_share)+'</td><td class="n">'+pct(b.coverage)+'</td></tr>';});
 html+='</table><div class="caveat"><b>Where this comes from.</b> USCG AIS via NOAA MarineCadastre, one position a minute from shore receivers, so tracks fade beyond roughly 30–50 nm and the long-range fleet is mostly seen leaving and returning ("AIS" is the share of each trip with a position). A stop is a run below '+m.stop_rule.drift_max_kt+' kt for '+m.stop_rule.drift_min_min+'+ min, or a looping run at '+m.stop_rule.troll_kt[0]+'–'+m.stop_rule.troll_kt[1]+' kt for '+m.stop_rule.troll_min_min+'+ min. Stops within '+m.harbor_km+' km of a landing are never counted; stops within '+m.nearshore_km+' km (bait grounds, the kelp edge) are hidden unless you tick "include near-dock stops".'
  +(m.count_joined_trips?' Dock counts joined for '+m.count_joined_trips+' trips (sportfishingreport.com).':'')+'</div>';
 return html;}

function boatPanel(i,n){var b=D.boats[i];
 var trips=D.trips.filter(function(t){return t[0]===i;}).sort(function(a,c){return a[1]<c[1]?-1:1;});
 var html='<div class="navrow"><button class="chip" data-act="fleet">‹ All boats</button></div>'
  +'<h3>'+esc(b.name)+' <span class="mut">· '+esc(short(b.landing))+'</span></h3>'
  +'<div class="mut">'+(b.length_ft?b.length_ft+' ft · ':'')+(b.trip_type?esc(b.trip_type)+' · ':'')+'MMSI '+b.mmsi+'</div>';
 if(S.trip){var tt=trips.filter(function(t){return t[13]===S.trip[1];})[0];
  html+='<div class="banner"><span>Showing only the trip that left <b>'+(tt?fmtDate(tt[1]):'')+'</b></span><button class="chip" data-act="alltrips">Show all trips</button></div>';}
 if(!n)html+='<div class="empty">No stops for this boat in this selection. Try <button class="chip" data-act="week" data-w="-1">all weeks</button>.</div>';
 html+='<div class="stat"><span>Trips seen</span><b>'+b.trips+'</b></div>'
  +'<div class="stat"><span>Time stopped and fishing</span><b>'+hrs(b.fish_min)+'</b></div>'
  +'<div class="stat"><span>Farthest from the dock</span><b>'+(b.max_km?nm(b.max_km):'—')+'</b></div>'
  +'<div class="stat"><span>Share of trip time with an AIS position</span><b>'+pct(b.coverage)+'</b></div>'
  +'<div class="stat"><span>Next trip: same 0.5 km spot / within 1.5 km</span><b>'+pct(b.return_same_spot)+' / '+pct(b.return_same_area)+'</b></div>'
  +'<div class="stat"><span>Home zone: top-cell share · cells for half</span><b>'+pct(b.top_share)+' · '+(b.cells_for_half||'—')+'</b></div>'
  +'<div class="stat"><span>Fishing days with '+CONV_BOATS+'+ fleet boats in the same cell</span><b>'+pct(b.convergence_share)+'</b></div>'
  +'<h4>Trips <span class="mut">· click one to see just its stops</span></h4><table><tr><th>Left</th><th class="n">Hours</th><th class="n">Max nm</th><th class="n">Stops</th><th class="n">Fishing</th><th class="n">Dock count</th></tr>';
 trips.forEach(function(t){var sel=S.trip&&S.trip[1]===t[13];
  html+='<tr class="row'+(sel?' sel':'')+'" data-act="trip" data-t="'+t[13]+'"><td>'+fmtDate(t[1])+'</td><td class="n">'+t[2]+'</td><td class="n">'+Math.round(t[3]/1.852)+'</td><td class="n">'+t[5]+'</td><td class="n">'+hrs(t[6])+'</td><td class="n">'+(t[10]!=null?t[10]+' fish / '+t[9]+' anglers':'<span class="mut">—</span>')+'</td></tr>';});
 html+='</table><div class="caveat">A trip with 0 stops and a big "Max nm" went out of AIS range: the boat was seen leaving and coming back, not fishing. A dock count is the landing\'s posted total for that boat on the day it returned; multi-day trips get one count for all their stops.</div>';
 return html;}

function cellPanel(h,c){var ctr=D.cells[h].c;
 var byBoat=Object.keys(c.byBoat).map(function(b){return [b,c.byBoat[b]];}).sort(function(a,b){return b[1]-a[1];});
 var days=Object.keys(c.days).sort();
 var k=D.cell_catch[h];
 var back=isSingleBoat()?'‹ '+esc(D.boats[boatIdx()].name):'‹ Whole fleet';
 var f=_fmt({lat:ctr[0],lng:ctr[1]});
 var html='<div class="navrow"><button class="chip" data-act="uncell">'+back+'</button></div>'
  +'<h3>One cell, centre '+f.dd+'</h3>'
  +'<div class="mut">'+f.dm+' · about 1.4 km across · '+(S.week>=0?'week of '+fmtDate(D.weeks[S.week]):'whole period')+'</div>'
  +'<div class="stat"><span>Time stopped and fishing</span><b>'+hrs(c.fish)+(c.troll?' ('+Math.round(100*c.troll/c.fish)+'% trolling)':'')+'</b></div>'
  +'<div class="stat"><span>Boats · trips · days</span><b>'+c.nBoats+' · '+c.nTrips+' · '+c.nDays+'</b></div>'
  +'<div class="stat"><span>Days with '+CONV_BOATS+'+ boats here</span><b>'+c.conv+'</b></div>'
  +(k?'<div class="stat"><span>Fish kept per angler (counted trips)</span><b>'+k[0]+' on '+k[1]+'</b></div>':'')
  +'<h4>Who fished it</h4><table>';
 byBoat.slice(0,12).forEach(function(x){html+='<tr class="row" data-act="boat" data-b="'+x[0]+'"><td>'+esc(D.boats[x[0]].name)+'</td><td class="mut">'+esc(short(D.boats[x[0]].landing))+'</td><td class="n">'+hrs(x[1])+'</td></tr>';});
 html+='</table><h4>When</h4><div class="mut" style="line-height:1.7">'+days.map(function(d){var n=Object.keys(c.days[d]).length;return '<span'+(n>=CONV_BOATS?' style="color:#fbbf24"':'')+'>'+fmtDate(d)+(n>1?' ×'+n+' boats':'')+'</span>';}).join(' · ')+'</div>';
 return html;}

/* ---------- controls ---------- */
function setWeek(i){i=Math.max(-1,Math.min(D.weeks.length-1,i));S.week=i;commit();}
function togglePlay(){var b=document.getElementById('play');
 if(S.playing){clearInterval(S.playing);S.playing=null;b.innerHTML='&#9654; Play';b.classList.remove('on');return;}
 b.innerHTML='&#10074;&#10074; Pause';b.classList.add('on');
 if(S.week<0||S.week>=D.weeks.length-1)setWeek(0);
 S.playing=setInterval(function(){if(S.week>=D.weeks.length-1){togglePlay();return;}S.week+=1;syncControls();draw();},1100);}
function stopPlay(){if(S.playing)togglePlay();}

function boot(d){D=d;
 D.weeks.forEach(function(w,i){WEEKIDX[w]=i;});
 D.boats.forEach(function(b,i){MMSI_IDX[String(b.mmsi)]=i;});
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];
  var dt=new Date(s[1]+'T12:00:00Z');var mon=new Date(dt);mon.setUTCDate(dt.getUTCDate()-((dt.getUTCDay()+6)%7));
  var key=mon.toISOString().slice(0,10);STOPWEEK[i]=WEEKIDX[key]==null?-2:WEEKIDX[key];}
 map=L.map('map',{zoomControl:true,attributionControl:false});
 map.fitBounds([[32.4,-119.7],[34.0,-116.9]],{paddingTopLeft:[0,70],paddingBottomRight:[window.innerWidth>760?380:0,0]});
 var landLayer=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(landLayer);}).catch(function(){});
 borderLayer=L.layerGroup().addTo(map);
 L.polyline(MX_BORDER,{color:'#f87171',weight:2,dashArray:'6 6',opacity:.85,interactive:false}).addTo(borderLayer);
 L.marker(MX_BORDER[2],{interactive:false,icon:L.divIcon({className:'',html:'',iconSize:[0,0]})}).bindTooltip('US / Mexico maritime boundary',{permanent:true,direction:'right',offset:[8,0],className:'border-lbl'}).addTo(borderLayer);
 hexLayer=L.layerGroup().addTo(map);stopLayer=L.layerGroup().addTo(map);portLayer=L.layerGroup().addTo(map);
 Object.keys(D.landings).forEach(function(n){var p=D.landings[n];
  L.circleMarker([p[0],p[1]],{radius:3,weight:1,color:'#fbbf24',fillColor:'#b45309',fillOpacity:.8}).bindTooltip(n,{className:'lbl',permanent:map.getZoom()>=9,direction:'right'}).addTo(portLayer);});
 map.on('zoomend',function(){portLayer.eachLayer(function(l){var t=l.getTooltip();if(t){t.options.permanent=map.getZoom()>=9;l.unbindTooltip().bindTooltip(t.getContent(),t.options);}});drawStops();
  document.getElementById('legend').innerHTML=legendHtml(1,Object.keys(LAST_AGG).length);placeReadout();});
 window.addEventListener('resize',placeReadout);
 coordbox=document.getElementById('coordbox');setReadout(map.getCenter());
 map.on('mousemove',function(e){setReadout(e.latlng);});
 map.on('click',function(e){if(S.pick)dropWp(e.latlng);});
 map.on('contextmenu',function(e){dropWp(e.latlng);});
 map.on('popupopen',wireCopy);
 document.getElementById('gps').onclick=togglePick;
 var ws=document.getElementById('week');ws.max=D.weeks.length-1;
 ws.oninput=function(){stopPlay();S.week=+ws.value;syncControls();draw();};
 ws.onchange=function(){commit();};
 document.getElementById('wprev').onclick=function(){stopPlay();setWeek(S.week<0?D.weeks.length-1:S.week-1);};
 document.getElementById('wnext').onclick=function(){stopPlay();setWeek(S.week<0?0:S.week+1);};
 document.getElementById('wall').onclick=function(){stopPlay();setWeek(-1);};
 document.getElementById('play').onclick=togglePlay;
 document.getElementById('metric').onchange=function(e){S.metric=e.target.value;commit();};
 document.getElementById('kind').onchange=function(e){S.kind=e.target.value;commit();};
 document.getElementById('nearshore').onchange=function(e){S.nearshore=e.target.checked;commit();};
 var sel=document.getElementById('boat');
 var ports={};D.boats.forEach(function(b){if(b.port&&b.trips>0)(ports[b.port]=ports[b.port]||{})[b.landing]=1;});
 Object.keys(ports).sort().forEach(function(p){var og=document.createElement('optgroup');og.label=p;
  var o=document.createElement('option');o.value='P:'+p;o.textContent='All '+p+' boats';og.appendChild(o);
  Object.keys(ports[p]).sort().forEach(function(l){var o2=document.createElement('option');o2.value='L:'+l;o2.textContent='  '+short(l)+' (landing)';og.appendChild(o2);
   D.boats.forEach(function(b,i){if(b.landing===l&&b.trips>0){var o3=document.createElement('option');o3.value=String(i);o3.textContent='    '+b.name;og.appendChild(o3);}});});
  sel.appendChild(og);});
 sel.onchange=function(e){S.boat=e.target.value;S.cell=null;S.trip=null;commit();};
 window.addEventListener('popstate',function(){stopPlay();readHash();syncControls();draw();});
 readHash();syncControls();draw();
 window.__fleetMap=map;}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('panel').innerHTML='<b>Could not load fleet data.</b><br/><span class="mut">data.json failed to fetch.</span>';});
