'use strict';
/* Fleet Tracks: where the SoCal sportfishing fleet actually stops, from public
   AIS. Everything aggregates client-side from data.json's stop list, so the
   boat / week / stop-kind filters are instant. CSP-clean: same-origin only.

   Navigation model: the view state S (boat, week window, metric, kind,
   nearshore, cell, trip) is mirrored into the URL hash on every change and
   restored from it on load and on popstate, so the browser's back/forward
   buttons walk through selections and links are shareable.

   Phones (<= 760 px): the header collapses to a Filters toggle, the week
   window is two selects, and the panel is a bottom sheet that starts
   collapsed and opens when something is selected. */
var LAND_URL='/data/land.geojson';
var CONV_BOATS=3;          // "fleet piled in" = this many boats in one cell on one day
/* US / Mexico maritime boundary, Pacific: land terminus -> 12 nm point (1970
   treaty), then OP-1..OP-4 of the 1978 Treaty on Maritime Boundaries (UN
   DOALOS text). Degrees from the treaty's D-M-S. */
var MX_BORDER=[[32.5344,-117.1249],[32.58948,-117.46373],[32.62694,-117.82528],[31.13278,-118.60500],[30.54200,-121.86621]];
var D,map,hexLayer,stopLayer,portLayer,borderLayer,structLayer,spotLayer,liveLayer,wpMarker,coordbox,LIVE=null,STOPWEEK=[],WEEKIDX={},MMSI_IDX={};
/* w0..w1 = inclusive week window (indices into D.weeks); both -1 = whole period */
var S={w0:-1,w1:-1,metric:'fish',boat:'all',kind:'all',nearshore:false,cell:null,trip:null,playing:null,pick:false};
var LAST_AGG={};

function isMobile(){return window.innerWidth<=760;}
function fmtDate(s){var d=new Date(s+'T12:00:00Z');return d.toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});}
/* With more than one year on the timeline every date needs its year. */
function multiYear(){return D&&D.meta&&D.meta.start.slice(0,4)!==D.meta.end.slice(0,4);}
function fmtDateY(s){return fmtDate(s)+(multiYear()?' ’'+s.slice(2,4):'');}
function hrs(m){return m<60?Math.round(m)+' min':(m/60).toFixed(m<600?1:0)+' h';}
function pct(x){return x==null?'—':Math.round(x*100)+'%';}
function nm(km){return Math.round(km/1.852)+' nm';}
function short(l){return String(l||'').replace(/ Sportfishing| Landing| Sea Center/g,'').replace("Davey's Locker","Davey's");}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function isSingleBoat(){return S.boat!=='all'&&S.boat.slice(0,2)!=='L:'&&S.boat.slice(0,2)!=='P:';}
function boatIdx(){return isSingleBoat()?+S.boat:-1;}
function allWeeks(){return S.w0<0;}
function weekLabel(){if(allWeeks())return 'whole period';
 return S.w0===S.w1?'week of '+fmtDateY(D.weeks[S.w0]):'weeks of '+fmtDateY(D.weeks[S.w0])+' – '+fmtDateY(D.weeks[S.w1]);}
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
 if(!allWeeks())p.push('weeks='+D.weeks[S.w0]+(S.w1!==S.w0?'..'+D.weeks[S.w1]:''));
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
 S.w0=S.w1=-1;
 var wk=q.weeks||q.week;
 if(wk){var ab=wk.split('..');var a=WEEKIDX[ab[0]],b=WEEKIDX[ab[1]||ab[0]];if(a!=null&&b!=null){S.w0=Math.min(a,b);S.w1=Math.max(a,b);}}
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
 document.getElementById('w0').value=allWeeks()?'all':String(S.w0);
 document.getElementById('w1').value=allWeeks()?'all':String(S.w1);
 document.getElementById('wall').classList.toggle('on',allWeeks());
 document.getElementById('wlabel').textContent=allWeeks()?(multiYear()?fmtDateY(D.meta.start)+' – '+fmtDateY(D.meta.end):fmtDate(D.meta.start)+' – '+fmtDate(D.meta.end)+' '+D.meta.end.slice(0,4)):(S.w1-S.w0+1)+' week'+(S.w1>S.w0?'s':'');}

/* ---------- filtering + aggregation ---------- */
function boatMatch(bi){if(S.boat==='all')return true;
 if(S.boat.slice(0,2)==='L:')return D.boats[bi].landing===S.boat.slice(2);
 if(S.boat.slice(0,2)==='P:')return D.boats[bi].port===S.boat.slice(2);
 return bi===+S.boat;}
function stopVisible(s,i){
 if(!boatMatch(s[0]))return false;
 if(S.kind!=='all'&&s[5]!==+S.kind)return false;
 if(!S.nearshore&&s[8])return false;
 if(!allWeeks()&&(STOPWEEK[i]<S.w0||STOPWEEK[i]>S.w1))return false;
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
  if(!isMobile())poly.bindTooltip(tipHtml(h,c),{className:'cell-tip',sticky:true,direction:'top',offset:[0,-6]});
  poly.on('click',function(e){if(S.pick){dropWp(e.latlng);return;}S.cell=(S.cell===h)?null:h;if(isMobile())openSheet(!!S.cell);commit();});
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
 var html='<div><b>Hexagons</b> = '+metricLabel()+'<br/>'+esc(showing)+' · '+weekLabel()+(S.kind==='0'?' · drifting only':S.kind==='1'?' · trolling only':'')+'</div>'
  +'<div style="margin:4px 0">'+steps.map(function(t){return '<span class="sw" style="background:'+color(t)+'"></span>'+f(t)+'&nbsp;&nbsp;';}).join('')+'</div>';
 if(!n)html+='<div>Nothing to show for this selection.</div>';
 else if(map.getZoom()<10&&!isSingleBoat())html+='<div class="mobhide">Each cell ≈ 1.4 km across. Zoom in to see individual stops.</div>';
 else html+='<div class="mobhide">Dots are individual stops: white = drifting/anchored, blue = trolling. Bigger = longer.</div>';
 html+='<div class="mobhide"><span class="ln"></span>US / Mexico maritime boundary · AIS coverage ends well before it</div>';
 if(map.hasLayer(liveLayer))html+=liveSummary();
 return html;}

function drawStops(){stopLayer.clearLayers();
 var showAll=map.getZoom()>=10,single=isSingleBoat();
 if(!showAll&&!single)return;
 var bounds=[];
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];if(!stopVisible(s,i))continue;
  if(S.trip!=null&&!(s[0]===S.trip[0]&&s[7]===S.trip[1]))continue;
  var r=Math.max(2,Math.min(9,Math.sqrt(s[4]/8)));
  if(S.trip)bounds.push([s[2],s[3]]);
  var m=L.circleMarker([s[2],s[3]],{radius:r,weight:s[5]===1?1.5:0.5,color:s[5]===1?'#38bdf8':'#fff',fillColor:s[5]===1?'#0ea5e9':'#f8fafc',fillOpacity:.55});
  if(!isMobile())m.bindTooltip(esc(D.boats[s[0]].name)+' · '+fmtDate(s[1])+' · '+hrs(s[4])+(s[5]===1?' trolling':' drifting / anchored'),{className:'cell-tip',direction:'top'});
  m.addTo(stopLayer);}
 if(S.trip&&bounds.length&&S._fitTrip){S._fitTrip=false;
  map.fitBounds(bounds,{padding:[30,30],maxZoom:11,paddingTopLeft:[0,isMobile()?110:70],paddingBottomRight:[isMobile()?0:380,isMobile()?Math.round(window.innerHeight*0.5):0]});}}

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
 var lb=el.querySelector('[data-live-boat]');if(lb){lb.onclick=function(){S.boat=lb.getAttribute('data-live-boat');S.cell=null;S.trip=null;map.closePopup();if(isMobile())openSheet(true);commit();};}
 el.querySelectorAll('.copybtn').forEach(function(b){b.onclick=function(){
  if(b.getAttribute('data-rm')){removeWp();return;}
  var txt=b.getAttribute('data-c');
  var done=function(){toast('Copied '+txt);};
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(done,function(){toast(txt);});
  else toast(txt);};});}
function togglePick(){S.pick=!S.pick;document.getElementById('gps').classList.toggle('on',S.pick);map.getContainer().classList.toggle('pick',S.pick);setReadout(map.getCenter());
 if(S.pick&&isMobile())toast('Tap the map to drop a waypoint');}

/* ---------- reference overlays: structure + dive spots ---------- */
var STRUCT_STYLE={bank:['#8b5cf6','#a78bfa',4],seamount:['#8b5cf6','#a78bfa',4.5],reef:['#0e7490','#22d3ee',3],rock:['#0e7490','#22d3ee',2.5],
 'community-spot':['#6d28d9','#c4b5fd',2.5],anchorage:['#475569','#94a3b8',2.5],landmark:['#475569','#94a3b8',2.5],islands:['#475569','#94a3b8',3],trough:['#1e3a8a','#60a5fa',3]};
var REFLBL={permanent:true,direction:'right',offset:[5,0],className:'ref-lbl',interactive:false};
function refClass(){var z=map.getZoom();return z>=10?'z10':z>=9?'z9':z>=8?'z8':'';}
function syncRefZoom(){var c=map.getContainer();c.classList.remove('z8','z9','z10');var k=refClass();if(k)c.classList.add(k);}
function structPopup(p){var depth=p.minDepthFt?'top ~'+p.minDepthFt+' ft':'';
 return '<div class="ref"><b>'+esc(p.name)+'</b><br/><span class="mut">'+esc(p['class'].replace('-',' '))+(depth?' · '+depth:'')+'</span>'
  +(p.description?'<br/>'+esc(p.description):'')+(p.commonSpecies&&p.commonSpecies.length?'<br/><span class="mut">'+esc(p.commonSpecies.join(', '))+'</span>':'')+'</div>';}
function drawReference(){
 /* Structure: the main app's named banks / seamounts / reefs, read at runtime
    so the two surfaces never disagree. Labels are gated by zoom + tier. */
 fetch('/data/bathy-features.geojson').then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(!g||!structLayer)return;
  g.features.forEach(function(f){var c=f.geometry.coordinates,lat=c[1],lng=c[0],p=f.properties;
   if(lat<31||lat>34.8||lng<-121.5||lng>-116.8)return;
   var st=STRUCT_STYLE[p['class']]||STRUCT_STYLE.landmark,tier=p.importanceTier||'minor';
   var m=L.circleMarker([lat,lng],{radius:st[2],weight:1.2,color:st[1],fillColor:st[0],fillOpacity:.75});
   m.bindTooltip(p.shortName||p.name,Object.assign({},REFLBL,{className:'ref-lbl '+tier}));
   m.bindPopup(structPopup(p),{closeButton:false,maxWidth:260});
   m.addTo(structLayer);});}).catch(function(){});
 if(spotLayer&&D.reference&&D.reference.spots){D.reference.spots.forEach(function(s){
  L.circleMarker([s.lat,s.lng],{radius:2.5,weight:1,opacity:.6,color:'#7dd3fc',fillColor:'#0e7490',fillOpacity:.4})
   .bindTooltip(s.name,Object.assign({},REFLBL,{className:'ref-lbl spot'})).bindPopup('<div class="ref"><b>'+esc(s.name)+'</b><br/><span class="mut">dive spot (ShouldIDive)</span></div>',{closeButton:false}).addTo(spotLayer);});}}
function refPrefs(){try{return JSON.parse(localStorage.getItem('sd:fleet:layers')||'{}');}catch{return {};}}
function saveRefPrefs(){try{localStorage.setItem('sd:fleet:layers',JSON.stringify({structure:map.hasLayer(structLayer),spots:map.hasLayer(spotLayer),landings:map.hasLayer(portLayer),live:map.hasLayer(liveLayer)}));}catch{}}

/* ---------- "Right now": the fleet's latest live AIS positions ---------- */
var LIVE_URL='/api/fleet/now',LIVE_EVERY_MS=180000;
function ageMin(iso){return Math.max(0,Math.round((Date.now()-Date.parse(iso))/60000));}
function ageText(m){return m<1?'just now':m<60?m+' min ago':m<1440?Math.round(m/60)+' h ago':Math.round(m/1440)+' d ago';}
function liveIcon(b,fresh){var rot=(b.heading!=null?b.heading:(b.cog!=null?b.cog:0));var moving=(b.sog||0)>=1;
 var col=fresh?'#4ade80':'#86efac',op=fresh?1:.55;
 var html=moving?'<div class="lv" style="transform:rotate('+rot+'deg);opacity:'+op+'"><svg width="18" height="18" viewBox="0 0 18 18"><path d="M9 1 L15 16 L9 12.5 L3 16 Z" fill="'+col+'" stroke="#052e16" stroke-width="1"/></svg></div>'
  :'<div class="lv" style="opacity:'+op+'"><svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="'+col+'" stroke="#052e16" stroke-width="1.5"/></svg></div>';
 return L.divIcon({className:'',html:html,iconSize:[18,18],iconAnchor:[9,9]});}
function drawLive(){if(!liveLayer)return;liveLayer.clearLayers();if(!LIVE||!LIVE.boats)return;
 LIVE.boats.forEach(function(b){var bi=MMSI_IDX[String(b.mmsi)];if(bi==null)return;   // roster boats only
  var boat=D.boats[bi],a=ageMin(b.t),fresh=a<=45,moving=(b.sog||0)>=1;
  var m=L.marker([b.lat,b.lon],{icon:liveIcon(b,fresh),zIndexOffset:900,interactive:true});
  var txt='<div class="ref"><b>'+esc(boat.name)+'</b> <span class="mut">· '+esc(short(boat.landing))+'</span><br/>'
   +(moving?'under way '+(b.sog||0).toFixed(1)+' kt, heading '+Math.round(b.heading!=null?b.heading:b.cog)+'°':'stopped')+'<br/><span class="mut">'+ageText(a)+' · '+b.t.replace('T',' ').slice(0,16)+' UTC</span>'
   +'<br/><button class="chip" data-live-boat="'+bi+'">This boat\'s trips</button></div>';
  m.bindPopup(txt,{closeButton:false,maxWidth:240});
  m.bindTooltip(esc(boat.name)+' · '+ageText(a),{className:'ref-lbl live',direction:'right',offset:[8,0],permanent:false,interactive:false});
  m.addTo(liveLayer);});}
function fetchLive(){fetch(LIVE_URL,{cache:'no-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(j){
  if(!j)return;LIVE=j;drawLive();var lg=document.getElementById('legend');if(lg)lg.innerHTML=legendHtml(1,Object.keys(LAST_AGG).length);placeReadout();}).catch(function(){});}
function liveSummary(){if(!LIVE||!LIVE.boats)return '';var n=0,fresh=0;LIVE.boats.forEach(function(b){if(MMSI_IDX[String(b.mmsi)]!=null){n++;if(ageMin(b.t)<=45)fresh++;}});
 if(!n)return '<div><span class="lvdot"></span>Right now: no live positions yet</div>';
 return '<div><span class="lvdot"></span><b>Right now</b>: '+n+' boats heard, '+fresh+' in the last 45 min · freshest '+(LIVE.freshest?ageText(ageMin(LIVE.freshest)):'—')+'</div>';}

/* ---------- panel / bottom sheet ---------- */
function openSheet(open){var p=document.getElementById('panel');p.classList.toggle('min',!open);document.body.classList.toggle('sheet-open',open&&isMobile());var c=p.querySelector('.chev');if(c)c.innerHTML=open?'&#9660;':'&#9650;';}
function renderPanel(A,n){var p=document.getElementById('panel');
 if(!p._wired){p._wired=true;p.addEventListener('click',onPanelClick);}
 var r;
 if(S.cell&&A[S.cell])r=cellPanel(S.cell,A[S.cell]);
 else if(isSingleBoat())r=boatPanel(boatIdx(),n);
 else r=fleetPanel(n);
 /* One shape for every view: a head (back button + title, plus a chevron on
    phones where the head is the sheet's handle) and a body the phone sheet
    hides when collapsed. */
 p.innerHTML='<div class="sheet-head">'+(r.nav?'<div class="navrow">'+r.nav+'</div>':'')+'<h3>'+r.title+'</h3><span class="chev mob">'+(p.classList.contains('min')?'&#9650;':'&#9660;')+'</span></div><div class="sheet-body">'+r.body+'</div>';}

function onPanelClick(e){var p=document.getElementById('panel');
 var el=e.target.closest('[data-act]');
 if(el){var act=el.getAttribute('data-act');
  if(act==='fleet'){S.boat='all';S.cell=null;S.trip=null;}
  else if(act==='boat'){S.boat=el.getAttribute('data-b');S.cell=null;S.trip=null;}
  else if(act==='uncell'){S.cell=null;}
  else if(act==='trip'){var tid=+el.getAttribute('data-t'),bi=boatIdx();S.trip=(S.trip&&S.trip[1]===tid)?null:[bi,tid];S._fitTrip=!!S.trip;}
  else if(act==='alltrips'){S.trip=null;}
  else if(act==='weeks'){S.w0=S.w1=-1;}
  if(isMobile()&&S.trip)openSheet(false);   // show the map once a trip is isolated
  commit();return;}
 if(e.target.closest('.sheet-head')&&isMobile())openSheet(p.classList.contains('min'));}

function howTo(){var seen=isMobile();try{seen=seen||localStorage.getItem('sd:fleet:seen')==='1';localStorage.setItem('sd:fleet:seen','1');}catch{seen=true;}
 return '<details class="how"'+(seen?'':' open')+'><summary>How to read this map</summary><ul>'
  +'<li>Each hexagon is about 1.4 km across. The brighter it is, the more time the fleet spent <b>stopped and fishing</b> there, from each boat\'s public AIS track.</li>'
  +'<li><b>Green arrows and dots</b> are the fleet right now: where each boat was last heard, within about ten minutes. Tap one for speed, heading and age.</li>'
  +'<li>Pick a boat in the table below (or the Boat menu) to see only its stops and its trips. Tap a trip to isolate it on the map.</li>'
  +'<li>Choose a window of weeks with the two week menus; <b>‹ ›</b> slide it, <b>Play</b> sweeps it through the season, <b>All weeks</b> brings back the whole period.</li>'
  +'<li>Tap any hexagon for who fished it and when. Zoom in to see individual stops. Purple and teal markers are named structure (banks, seamounts, reefs); the layers button at bottom right toggles them and the dive spots.</li>'
  +'<li><b>⌖ GPS</b> (or a right-click / long-press anywhere) drops a waypoint you can copy coordinates from.</li>'
  +'<li>The browser Back button undoes a selection.</li></ul></details>';}

function fleetPanel(n){var m=D.meta,s=D.summary;
 var rows=D.boats.map(function(b,i){return [i,b];}).filter(function(x){return boatMatch(x[0])&&x[1].trips>0;})
  .sort(function(a,b){return b[1].fish_min-a[1].fish_min;});
 var scope=S.boat==='all'?'The SoCal sportfishing fleet':S.boat.slice(2)+' boats';
 var nav=S.boat!=='all'?'<button class="chip" data-act="fleet">‹ Fleet</button>':'';
 var html='<div class="mut">'+m.n_boats+' boats · '+m.n_trips+' trips · '+m.n_stops+' fishing stops · '+fmtDate(m.start)+' – '+fmtDate(m.end)+' '+m.end.slice(0,4)+'</div>'
  +howTo();
 if(!n)html+='<div class="empty">No stops match this selection. Try <button class="chip" data-act="weeks">all weeks</button> or another boat.</div>';
 if(S.boat==='all')html+='<h4>Three questions, whole fleet</h4>'
  +'<div class="ans"><div class="q">Does a boat go back to the same spot next trip?</div><div class="a">On average a boat puts <b>'+pct(s.return_same_area_mean)+'</b> of its next trip fishing time within about 1.5 km of where it fished last trip. The median boat puts '+pct(s.return_same_area_median)+' there ('+pct(s.return_same_spot_median)+' in the exact 0.5 km cells), so a few boats are loyal to a spot and most roam. '+(s.trip_pairs||0)+' trip pairs.</div></div>'
  +'<div class="ans"><div class="q">Does it work a home zone?</div><div class="a">The median boat spends <b>'+pct(s.home_top_share_median)+'</b> of its season in one '+(m.h3_res===7?'1.4':'0.5')+' km cell and needs <b>'+(s.home_cells_for_half_median||'—')+'</b> cells to cover half its fishing time.</div></div>'
  +'<div class="ans"><div class="q">Does the fleet pile onto a bite?</div><div class="a"><b>'+(s.convergence_days_total||0)+'</b> boat-days had '+CONV_BOATS+' or more fleet boats in the same cell; the median boat spends <b>'+pct(s.convergence_share_median)+'</b> of its fishing days in one of those.</div></div>';
 html+='<h4>Boats <span class="mut">· tap one</span></h4><table class="boats"><tr><th>Boat</th><th class="n">Trips</th><th class="n">Fishing</th><th class="n" title="share of the next trip within 1.5 km of the previous trip">Same area</th><th class="n" title="share of the season in the top cell">Home</th><th class="n" title="share of trip time with an AIS position">AIS</th></tr>';
 rows.forEach(function(x){var b=x[1];html+='<tr class="row" data-act="boat" data-b="'+x[0]+'"><td>'+esc(b.name)+'<br/><span class="mut">'+esc(short(b.landing))+'</span></td><td class="n">'+b.trips+'</td><td class="n">'+hrs(b.fish_min)+'</td><td class="n">'+pct(b.return_same_area)+'</td><td class="n">'+pct(b.top_share)+'</td><td class="n">'+pct(b.coverage)+'</td></tr>';});
 html+='</table><div class="caveat"><b>Where this comes from.</b> USCG AIS via NOAA MarineCadastre, one position a minute from shore receivers, so tracks fade beyond roughly 30–50 nm and the long-range fleet is mostly seen leaving and returning ("AIS" is the share of each trip with a position). A stop is a run below '+m.stop_rule.drift_max_kt+' kt for '+m.stop_rule.drift_min_min+'+ min, or a looping run at '+m.stop_rule.troll_kt[0]+'–'+m.stop_rule.troll_kt[1]+' kt for '+m.stop_rule.troll_min_min+'+ min. Stops within '+m.harbor_km+' km of a landing are never counted; stops within '+m.nearshore_km+' km (bait grounds, the kelp edge) are hidden unless you tick "include near-dock stops".'
  +(m.count_joined_trips?' Dock counts joined for '+m.count_joined_trips+' trips (sportfishingreport.com).':'')+'</div>';
 return {nav:nav,title:esc(scope)+', from AIS',body:html};}

function boatPanel(i,n){var b=D.boats[i];
 var trips=D.trips.filter(function(t){return t[0]===i;}).sort(function(a,c){return a[1]<c[1]?-1:1;});
 var html='<div class="mut">'+(b.length_ft?b.length_ft+' ft · ':'')+(b.trip_type?esc(b.trip_type)+' · ':'')+'MMSI '+b.mmsi+'</div>';
 if(S.trip){var tt=trips.filter(function(t){return t[13]===S.trip[1];})[0];
  html+='<div class="banner"><span>Showing only the trip that left <b>'+(tt?fmtDate(tt[1]):'')+'</b></span><button class="chip" data-act="alltrips">Show all trips</button></div>';}
 if(!n)html+='<div class="empty">No stops for this boat in this selection. Try <button class="chip" data-act="weeks">all weeks</button>.</div>';
 html+='<div class="stat"><span>Trips seen</span><b>'+b.trips+'</b></div>'
  +'<div class="stat"><span>Time stopped and fishing</span><b>'+hrs(b.fish_min)+'</b></div>'
  +'<div class="stat"><span>Farthest from the dock</span><b>'+(b.max_km?nm(b.max_km):'—')+'</b></div>'
  +'<div class="stat"><span>Share of trip time with an AIS position</span><b>'+pct(b.coverage)+'</b></div>'
  +'<div class="stat"><span>Next trip: same 0.5 km spot / within 1.5 km</span><b>'+pct(b.return_same_spot)+' / '+pct(b.return_same_area)+'</b></div>'
  +'<div class="stat"><span>Home zone: top-cell share · cells for half</span><b>'+pct(b.top_share)+' · '+(b.cells_for_half||'—')+'</b></div>'
  +'<div class="stat"><span>Fishing days with '+CONV_BOATS+'+ fleet boats in the same cell</span><b>'+pct(b.convergence_share)+'</b></div>'
  +'<h4>Trips <span class="mut">· tap one to see just its stops</span></h4><table class="trips"><tr><th>Left</th><th class="n">Hours</th><th class="n">Max nm</th><th class="n">Stops</th><th class="n">Fishing</th><th class="n">Dock count</th></tr>';
 trips.forEach(function(t){var sel=S.trip&&S.trip[1]===t[13];
  html+='<tr class="row'+(sel?' sel':'')+'" data-act="trip" data-t="'+t[13]+'"><td>'+fmtDate(t[1])+'</td><td class="n">'+t[2]+'</td><td class="n">'+Math.round(t[3]/1.852)+'</td><td class="n">'+t[5]+'</td><td class="n">'+hrs(t[6])+'</td><td class="n">'+(t[10]!=null?t[10]+' fish / '+t[9]+' anglers':'<span class="mut">—</span>')+'</td></tr>';});
 html+='</table><div class="caveat">A trip with 0 stops and a big "Max nm" went out of AIS range: the boat was seen leaving and coming back, not fishing. A dock count is the landing\'s posted total for that boat on the day it returned; multi-day trips get one count for all their stops.</div>';
 return {nav:'<button class="chip" data-act="fleet">‹ All boats</button>',title:esc(b.name)+' <span class="mut">· '+esc(short(b.landing))+'</span>',body:html};}

function cellPanel(h,c){var ctr=D.cells[h].c;
 var byBoat=Object.keys(c.byBoat).map(function(b){return [b,c.byBoat[b]];}).sort(function(a,b){return b[1]-a[1];});
 var days=Object.keys(c.days).sort();
 var k=D.cell_catch[h];
 var back=isSingleBoat()?'‹ '+esc(D.boats[boatIdx()].name):'‹ Fleet';
 var f=_fmt({lat:ctr[0],lng:ctr[1]});
 var html='<div class="mut">'+f.dd+' · '+f.dm+' · about 1.4 km across · '+weekLabel()+'</div>'
  +'<div class="stat"><span>Time stopped and fishing</span><b>'+hrs(c.fish)+(c.troll?' ('+Math.round(100*c.troll/c.fish)+'% trolling)':'')+'</b></div>'
  +'<div class="stat"><span>Boats · trips · days</span><b>'+c.nBoats+' · '+c.nTrips+' · '+c.nDays+'</b></div>'
  +'<div class="stat"><span>Days with '+CONV_BOATS+'+ boats here</span><b>'+c.conv+'</b></div>'
  +(k?'<div class="stat"><span>Fish kept per angler (counted trips)</span><b>'+k[0]+' on '+k[1]+'</b></div>':'')
  +'<h4>Who fished it</h4><table>';
 byBoat.slice(0,12).forEach(function(x){html+='<tr class="row" data-act="boat" data-b="'+x[0]+'"><td>'+esc(D.boats[x[0]].name)+'</td><td class="mut">'+esc(short(D.boats[x[0]].landing))+'</td><td class="n">'+hrs(x[1])+'</td></tr>';});
 html+='</table><h4>When</h4><div class="mut" style="line-height:1.7">'+days.map(function(d){var n=Object.keys(c.days[d]).length;return '<span'+(n>=CONV_BOATS?' style="color:#fbbf24"':'')+'>'+fmtDate(d)+(n>1?' ×'+n+' boats':'')+'</span>';}).join(' · ')+'</div>';
 return {nav:'<button class="chip" data-act="uncell">'+back+'</button>',title:'One cell · '+hrs(c.fish)+' fishing',body:html};}

/* ---------- week window controls ---------- */
function setRange(a,b){var n=D.weeks.length;
 if(a<0||b<0){S.w0=S.w1=-1;}
 else{a=Math.max(0,Math.min(n-1,a));b=Math.max(0,Math.min(n-1,b));S.w0=Math.min(a,b);S.w1=Math.max(a,b);}
 commit();}
function width(){return allWeeks()?1:S.w1-S.w0+1;}
function togglePlay(){var b=document.getElementById('play');
 if(S.playing){clearInterval(S.playing);S.playing=null;b.innerHTML='&#9654; Play';b.classList.remove('on');return;}
 b.innerHTML='&#10074;&#10074; Pause';b.classList.add('on');
 var w=width(),n=D.weeks.length;
 if(allWeeks()||S.w1>=n-1)setRange(0,w-1);
 S.playing=setInterval(function(){if(S.w1>=n-1){togglePlay();return;}S.w0+=1;S.w1+=1;syncControls();draw();},1100);}
function stopPlay(){if(S.playing)togglePlay();}

function boot(d){D=d;
 D.weeks.forEach(function(w,i){WEEKIDX[w]=i;});
 D.boats.forEach(function(b,i){MMSI_IDX[String(b.mmsi)]=i;});
 for(var i=0;i<D.stops.length;i++){var s=D.stops[i];
  var dt=new Date(s[1]+'T12:00:00Z');var mon=new Date(dt);mon.setUTCDate(dt.getUTCDate()-((dt.getUTCDay()+6)%7));
  var key=mon.toISOString().slice(0,10);STOPWEEK[i]=WEEKIDX[key]==null?-2:WEEKIDX[key];}
 map=L.map('map',{zoomControl:true,attributionControl:false});
 map.fitBounds(isMobile()?[[32.45,-118.6],[33.4,-117.0]]:[[32.4,-119.7],[34.0,-116.9]],{paddingTopLeft:[0,isMobile()?110:70],paddingBottomRight:[isMobile()?0:380,isMobile()?70:0]});
 var landLayer=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){
  if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(landLayer);}).catch(function(){});
 borderLayer=L.layerGroup().addTo(map);
 L.polyline(MX_BORDER,{color:'#f87171',weight:2,dashArray:'6 6',opacity:.85,interactive:false}).addTo(borderLayer);
 L.marker(MX_BORDER[2],{interactive:false,icon:L.divIcon({className:'',html:'',iconSize:[0,0]})}).bindTooltip('US / Mexico maritime boundary',{permanent:true,direction:'right',offset:[8,0],className:'border-lbl'}).addTo(borderLayer);
 hexLayer=L.layerGroup().addTo(map);stopLayer=L.layerGroup().addTo(map);
 var prefs=refPrefs();
 structLayer=L.layerGroup();spotLayer=L.layerGroup();portLayer=L.layerGroup();liveLayer=L.layerGroup();
 if(prefs.live!==false)liveLayer.addTo(map);
 if(prefs.structure!==false)structLayer.addTo(map);
 if(prefs.spots===true)spotLayer.addTo(map);
 if(prefs.landings!==false)portLayer.addTo(map);
 drawReference();syncRefZoom();
 L.control.layers(null,{'Right now: live positions':liveLayer,'Structure: banks, seamounts, reefs':structLayer,'Dive spots':spotLayer,'Landings':portLayer},{position:'bottomright',collapsed:true}).addTo(map);
 fetchLive();setInterval(fetchLive,LIVE_EVERY_MS);
 map.on('overlayadd overlayremove',function(){document.getElementById('legend').innerHTML=legendHtml(1,Object.keys(LAST_AGG).length);placeReadout();});
 map.on('overlayadd overlayremove',saveRefPrefs);
 Object.keys(D.landings).forEach(function(n){var p=D.landings[n];
  L.circleMarker([p[0],p[1]],{radius:3,weight:1,color:'#fbbf24',fillColor:'#b45309',fillOpacity:.8}).bindTooltip(n,{className:'lbl',permanent:map.getZoom()>=9,direction:'right'}).addTo(portLayer);});
 map.on('zoomend',function(){syncRefZoom();portLayer.eachLayer(function(l){var t=l.getTooltip();if(t){t.options.permanent=map.getZoom()>=9;l.unbindTooltip().bindTooltip(t.getContent(),t.options);}});drawStops();
  document.getElementById('legend').innerHTML=legendHtml(1,Object.keys(LAST_AGG).length);placeReadout();});
 window.addEventListener('resize',placeReadout);
 coordbox=document.getElementById('coordbox');setReadout(map.getCenter());
 map.on('mousemove',function(e){setReadout(e.latlng);});
 map.on('click',function(e){if(S.pick)dropWp(e.latlng);});
 map.on('contextmenu',function(e){dropWp(e.latlng);});
 map.on('popupopen',wireCopy);
 var bar=document.getElementById('bar'),fb=document.getElementById('filt');
 var closeFilters=function(){bar.classList.remove('open');fb.setAttribute('aria-expanded','false');fb.innerHTML='Filters &#9662;';};
 fb.onclick=function(){var o=bar.classList.toggle('open');fb.setAttribute('aria-expanded',o?'true':'false');fb.innerHTML=o?'Filters &#9652;':'Filters &#9662;';if(o&&isMobile())openSheet(false);};
 document.getElementById('gps').onclick=function(){togglePick();if(isMobile())closeFilters();};
 /* week window: two selects (first / last week) */
 var w0=document.getElementById('w0'),w1=document.getElementById('w1');
 [w0,w1].forEach(function(sel){var o=document.createElement('option');o.value='all';o.textContent='All';sel.appendChild(o);
  D.weeks.forEach(function(w,i){var o2=document.createElement('option');o2.value=String(i);o2.textContent=fmtDateY(w);sel.appendChild(o2);});});
 w0.onchange=function(){stopPlay();if(w0.value==='all')setRange(-1,-1);else setRange(+w0.value,allWeeks()?+w0.value:Math.max(+w0.value,S.w1));};
 w1.onchange=function(){stopPlay();if(w1.value==='all')setRange(-1,-1);else setRange(allWeeks()?+w1.value:Math.min(S.w0,+w1.value),+w1.value);};
 document.getElementById('wprev').onclick=function(){stopPlay();var n=D.weeks.length;if(allWeeks())setRange(n-1,n-1);else if(S.w0>0)setRange(S.w0-1,S.w1-1);};
 document.getElementById('wnext').onclick=function(){stopPlay();var n=D.weeks.length;if(allWeeks())setRange(0,0);else if(S.w1<n-1)setRange(S.w0+1,S.w1+1);};
 document.getElementById('wall').onclick=function(){stopPlay();setRange(-1,-1);};
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
 sel.onchange=function(e){S.boat=e.target.value;S.cell=null;S.trip=null;if(isMobile()){closeFilters();openSheet(isSingleBoat());}commit();};
 window.addEventListener('popstate',function(){stopPlay();readHash();syncControls();draw();});
 readHash();
 openSheet(!isMobile()||!!(S.cell||isSingleBoat()));
 syncControls();draw();
 window.__fleetMap=map;}

fetch('data.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(boot).catch(function(){
 document.getElementById('panel').innerHTML='<div class="sheet-head"><h3>Could not load fleet data.</h3></div><div class="sheet-body"><span class="mut">data.json failed to fetch.</span></div>';});
