'use strict';
/* Species Hotspots (dev): where the SoCal fleet caught each species, by time
   of year, measured in FISH. Four kinds of evidence, never blended into one
   number, each labelled where it appears:
   - AIS stops: a boat's posted dock count joined to its tracked trip; the
     fish are spread over the offshore stops AIS saw (hexagons, H3 res 6).
   - Report spots: a landing report sentence that names the species and a
     charted spot, with the fish quoted in that sentence (purple circles).
   - Left AIS range: trips that caught it but went past the shore receivers
     before a stop was seen (green arrows at the farthest point seen).
   - Landed at: every posted dock count, by landing (teal squares). Where the
     fish came home, not where they were caught: the fallback for species
     whose grounds AIS and the reports do not reach (wahoo, marlin).
   The calendar is every posted count, fleet-wide, AIS or not. */
var LAND_URL='/data/land.geojson';
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
var SEASONS=[['All year',[1,2,3,4,5,6,7,8,9,10,11,12]],['Spring',[3,4,5]],['Summer',[6,7,8]],['Fall',[9,10,11]],['Winter',[12,1,2]]];
var D,map,hexLayer,spotLayer,landLayer,pathLayer,CATS={},PATHS={},YEARS=[];
var S={sp:'all',months:[1,2,3,4,5,6,7,8,9,10,11,12],year:'all',src:'all',metric:'fish',sort:'fish',cell:null,spot:null,land:null,trip:null,exitSel:null};
var AGG={cells:{},spots:{},lands:{}};

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmtDate(s){var d=new Date(s.slice(0,10)+'T12:00:00Z');return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});}
function n0(x){return Math.round(x).toLocaleString('en-US');}
function pct(x){return Math.round(x*100)+'%';}
function spLabel(){return S.sp==='all'?'target species':CATS[S.sp].label.toLowerCase();}
function short(l){return String(l||'').replace(/ Sportfishing| Landing| Sea Center/g,'');}
var RAMP=[[30,41,59],[120,53,15],[217,119,6],[251,191,36],[254,240,138]];
function color(v){v=Math.max(0,Math.min(1,v));var p=v*(RAMP.length-1),i=Math.floor(p),f=p-i;var a=RAMP[i],b=RAMP[Math.min(i+1,RAMP.length-1)];
 return 'rgb('+Math.round(a[0]+(b[0]-a[0])*f)+','+Math.round(a[1]+(b[1]-a[1])*f)+','+Math.round(a[2]+(b[2]-a[2])*f)+')';}

/* ---------- selection ---------- */
var LAST12=['',''];
function monthOn(key){var y=key.slice(0,4),m=+key.slice(5,7);
 if(S.year==='12m'){var k=key.slice(0,7);if(k<LAST12[0]||k>LAST12[1])return false;}else if(S.year!=='all'&&y!==S.year)return false;
 return S.months.indexOf(m)>=0;}
function selLabel(){var m=S.months.slice().sort(function(a,b){return a-b;});
 var lbl=m.length===12?'all year':SEASONS.filter(function(s){return s[1].length===m.length&&s[1].every(function(x){return m.indexOf(x)>=0;});}).map(function(s){return s[0].toLowerCase();})[0]||m.map(function(x){return MON[x-1];}).join(', ');
 return lbl+(S.year==='all'?'':S.year==='12m'?' · last 12 months':' '+S.year);}
function catKeys(){return D.cats.map(function(c){return c.key;});}
function recFish(r){return S.sp==='all'?Object.keys(r.sp).reduce(function(a,k){return a+(k==='all'?0:r.sp[k]);},0):(r.sp[S.sp]||0);}
function recHas(r){return S.sp==='all'?Object.keys(r.sp).some(function(k){return k!=='all';}):r.sp[S.sp]!=null;}

/* Sum a {month:value} or {month:[a,b,..]} map over the selected months, also by year. */
function sumMonths(mm,idx){var out={t:0,y:{}};Object.keys(mm||{}).forEach(function(mo){if(!monthOn(mo))return;var v=idx==null?mm[mo]:mm[mo][idx];out.t+=v;out.y[mo.slice(0,4)]=(out.y[mo.slice(0,4)]||0)+v;});return out;}

function aggregate(){var cells={},spots={},lands={};
 var g=D.grid[S.sp]||{};
 Object.keys(D.effort).forEach(function(h){var e=sumMonths(D.effort[h],0),gc=g[h]||{};var pos=sumMonths(gc,0),fish=sumMonths(gc,1);
  if(e.t>0||pos.t>0){var mix={};if(S.sp==='all')catKeys().forEach(function(k){var f=sumMonths((D.grid[k]||{})[h]||{},1).t;if(f>0)mix[k]=f;});
   cells[h]={pos:pos.t,fish:fish.t,fy:fish.y,tot:e.t,mix:mix};}});
 Object.keys(D.rspots).forEach(function(name){var e=D.rspots[name],tot=sumMonths(e.tot).t;if(!tot)return;
  var pos=sumMonths((e.sp||{})[S.sp]||{}),fish=sumMonths((e.fish||{})[S.sp]||{}),mix={};
  if(S.sp==='all')catKeys().forEach(function(k){var f=sumMonths((e.fish||{})[k]||{}).t;if(f>0)mix[k]=f;});
  spots[name]={pos:pos.t,fish:fish.t,fy:fish.y,tot:tot,ll:e.ll,approx:e.approx,mix:mix};});
 var LD=D.landed[S.sp]||{};
 Object.keys(LD).forEach(function(l){var trips=sumMonths(LD[l],0),fish=sumMonths(LD[l],1);if(!trips.t)return;var mix={};
  if(S.sp==='all')catKeys().forEach(function(k){var f=sumMonths((D.landed[k]||{})[l]||{},1).t;if(f>0)mix[k]=f;});
  lands[l]={pos:trips.t,fish:fish.t,fy:fish.y,ll:(D.meta.landing_ll||{})[l],mix:mix};});
 var ports={};Object.keys(lands).forEach(function(l){var x=lands[l],pn=portOf(l),q=ports[pn];if(!x.ll)return;
  if(!q)q=ports[pn]={pos:0,fish:0,fy:{},mix:{},lat:0,lon:0,n:0,landings:[]};
  q.pos+=x.pos;q.fish+=x.fish;q.lat+=x.ll[0];q.lon+=x.ll[1];q.n++;q.landings.push(l);
  Object.keys(x.fy).forEach(function(y){q.fy[y]=(q.fy[y]||0)+x.fy[y];});Object.keys(x.mix).forEach(function(k){q.mix[k]=(q.mix[k]||0)+x.mix[k];});});
 Object.keys(ports).forEach(function(pn){var q=ports[pn];q.ll=[q.lat/q.n,q.lon/q.n];q.landings.sort(function(a,b){return lands[b].fish-lands[a].fish;});});
 AGG={cells:cells,spots:spots,lands:lands,ports:ports};return AGG;}
function portOf(l){return (D.meta.landing_port||{})[l]||l;}

function val(x,metric){return metric==='rate'?(x.tot?x.pos/x.tot:0):metric==='trips'?x.pos:x.fish;}

/* ---------- map ---------- */
/* port labels sit left of their squares; nearby ports (Mission Bay / San Diego, the LA harbor
   ports) are pushed apart in screen pixels, so this re-runs on zoom */
var LANDLBL=[],lblLayer;
function placeLandLabels(){if(!lblLayer)return;lblLayer.clearLayers();var sz=map.getSize();if(sz.x<1||sz.y<1)return;
 var items=LANDLBL.map(function(it){var pt=map.latLngToContainerPoint(it.ll);return {it:it,x:pt.x,y:pt.y,ay:pt.y};}).sort(function(a,b){return a.y-b.y;});
 items.forEach(function(a,i){for(var j=0;j<i;j++){var b=items[j];if(Math.abs(b.x-a.x)<170&&a.ay-b.ay<13)a.ay=b.ay+13;}
  L.marker(a.it.ll,{icon:L.divIcon({className:'',html:'',iconSize:[0,0]}),interactive:false})
   .bindTooltip(a.it.txt,{permanent:true,direction:'left',offset:[-a.it.sz/2-2,Math.round(a.ay-a.y)],className:'land-lbl'}).addTo(lblLayer);});}
function draw(){aggregate();hexLayer.clearLayers();spotLayer.clearLayers();landLayer.clearLayers();LANDLBL=[];
 var showAis=S.src==='all'||S.src==='ais',showRep=S.src==='all'||S.src==='rep',showLand=S.src==='all'||S.src==='land';
 var cells=AGG.cells,keys=Object.keys(cells),vmax=1;
 if(showAis){var vals=keys.map(function(h){return val(cells[h],S.metric);}).filter(function(v){return v>0;}).sort(function(a,b){return a-b;});
  vmax=vals.length?vals[Math.floor((vals.length-1)*0.97)]||vals[vals.length-1]:1;if(S.metric==='rate')vmax=Math.max(vmax,.05);
  keys.forEach(function(h){var c=cells[h],cg=D.cells[h];if(!cg)return;
   var v=val(c,S.metric),few=S.metric==='rate'&&c.tot<3;
   var t=v>0?(S.metric==='rate'?v/vmax:Math.sqrt(v/vmax)):0;
   var poly=L.polygon(cg.b,{color:h===S.cell?'#fff':'#0b1220',weight:h===S.cell?2:.5,fillColor:v>0?color(.18+.82*Math.min(1,t)):'#1e293b',fillOpacity:v>0?(few?.35:.8):.22,interactive:true});
   poly.bindTooltip(cellTip(h,c),{className:'cell-tip',sticky:true,direction:'top'});
   poly.on('click',function(){selectOnly('cell',S.cell===h?null:h);});
   poly.addTo(hexLayer);});
  (S.sp==='all'?[]:exitsSel()).forEach(function(x){var ic=L.divIcon({className:'',html:'<div class="ex" style="transform:rotate('+Math.round(x.brg)+'deg)"></div>',iconSize:[10,10],iconAnchor:[5,5]});
   L.marker(x.far,{icon:ic,zIndexOffset:600}).bindTooltip(esc(x.b)+' · '+fmtDate(x.d)+' · '+n0(recFish(x))+' fish<br/><span class="mut">farthest point AIS saw, '+x.nm+' nm out, heading '+compass(x.brg)+'; no stop seen</span>',{className:'cell-tip',direction:'top'})
    .on('click',function(){S.cell=null;S.spot=null;S.land=null;S.exitSel=x;S.trip=[x.m,x.id];drawTrip();renderPanel();}).addTo(spotLayer);});}
 if(showRep){var sp=AGG.spots,smax=1;Object.keys(sp).forEach(function(n){var v=sp[n].fish||sp[n].pos;if(v>smax)smax=v;});
  Object.keys(sp).forEach(function(n){var x=sp[n];if(!x.ll)return;var v=x.fish||x.pos,on=x.pos>0;
   var r=on?6+16*Math.sqrt(v/smax):3.5;
   var m=L.circleMarker(x.ll,{radius:r,weight:n===S.spot?3:2,color:on?'#a78bfa':'#475569',fillColor:'#7c3aed',fillOpacity:on?.28:0,dashArray:x.approx?'3 3':null});
   m.bindTooltip(spotTip(n,x),{className:'cell-tip',direction:'top'});
   if(on)L.marker(x.ll,{icon:L.divIcon({className:'',html:'',iconSize:[0,0]}),interactive:false})
    .bindTooltip(esc(n)+' · '+(x.fish?n0(x.fish)+' fish':x.pos+' reports'),{permanent:true,direction:'right',offset:[r+2,0],className:'spot-lbl'}).addTo(spotLayer);
   m.on('click',function(){selectOnly('spot',S.spot===n?null:n);});
   m.addTo(spotLayer);});}
 if(showLand){var pt=AGG.ports,lmax=1;Object.keys(pt).forEach(function(pn){if(pt[pn].fish>lmax)lmax=pt[pn].fish;});
  Object.keys(pt).forEach(function(pn){var x=pt[pn];if(!x.fish)return;var sz=Math.round(9+21*Math.sqrt(x.fish/lmax));
   var ic=L.divIcon({className:'',html:'<div class="land'+(pn===S.land?' sel':'')+'" style="width:'+sz+'px;height:'+sz+'px"></div>',iconSize:[sz,sz],iconAnchor:[sz/2,sz/2]});
   L.marker(x.ll,{icon:ic,zIndexOffset:500}).bindTooltip('<b>'+esc(pn)+'</b> landings<br/>'+x.landings.map(function(l){return esc(short(l))+' '+n0(AGG.lands[l].fish);}).join(' · ')
    +'<br/>'+n0(x.fish)+' '+esc(spLabel())+' on '+n0(x.pos)+' posted trips<br/><span class="mut">where they came home, not where they were caught</span>',{className:'cell-tip',direction:'top'})
    .on('click',function(){selectOnly('land',S.land===pn?null:pn);}).addTo(landLayer);
   LANDLBL.push({ll:x.ll,sz:sz,txt:esc(pn)+' · '+n0(x.fish)});});}
 placeLandLabels();
 document.getElementById('legend').innerHTML=legendHtml(vmax,showAis,showRep,showLand);
 renderPanel();}
function selectOnly(kind,v){S.cell=kind==='cell'?v:null;S.spot=kind==='spot'?v:null;S.land=kind==='land'?v:null;S.trip=null;S.exitSel=null;pathLayer.clearLayers();commit();}
function nearTxt(h){var c=D.cells[h];if(!c)return '';return c.near?'near '+esc(c.near[0])+(c.near[1]>=1?' ('+c.near[1].toFixed(0)+' nm)':''):c.c[0].toFixed(2)+', '+c.c[1].toFixed(2);}
function mixTxt(mix,n){return Object.keys(mix).filter(function(k){return mix[k]>=.5;}).sort(function(a,b){return mix[b]-mix[a];}).slice(0,n||3).map(function(k){return esc(CATS[k].label.split(' (')[0])+' '+n0(mix[k]);}).join(' · ');}
function cellTip(h,c){return '<b>'+nearTxt(h)+'</b><br/>~'+n0(c.fish)+' '+esc(spLabel())+' on '+c.pos+' of '+c.tot+' counted trips that stopped here'
 +(S.sp==='all'&&c.fish?'<br/>'+mixTxt(c.mix):'')+'<br/><span class="mut">'+selLabel()+' · AIS stops · click for the trips</span>';}
function spotTip(n,x){return '<b>'+esc(n)+'</b>'+(x.approx?' <span class="mut">(approx.)</span>':'')+'<br/>'+n0(x.fish)+' '+esc(spLabel())+' quoted in '+x.pos+' report sentences naming it'
 +(S.sp==='all'&&x.fish?'<br/>'+mixTxt(x.mix):'')+'<br/><span class="mut">'+selLabel()+' · landing reports · click for the reports</span>';}
function legendHtml(vmax,a,r,l){var h='<div><b style="color:#e2e8f0">'+esc(S.sp==='all'?'All target species':CATS[S.sp].label)+'</b> · '+selLabel()+'</div>';
 var anyHex=a&&Object.keys(AGG.cells).some(function(k){return val(AGG.cells[k],S.metric)>0;});
 if(!anyHex&&a)h+='<div class="lg-long" style="margin-top:4px">No AIS-stop hexagons for this selection</div>';
 if(anyHex){var vs=S.metric==='rate'?[.25,.5,.75,1].map(function(f){return f*vmax;}):[1,vmax*.25,vmax*.5,vmax].map(Math.round).filter(function(v,i,arr){return v>=1&&arr.indexOf(v)===i;});
  h+='<div class="lg-long" style="margin-top:4px">Hexagons (AIS stops): '+({fish:'fish caught by boats that stopped there',trips:'trips that caught it and stopped there',rate:'share of counted trips stopping there that caught it'})[S.metric]+'</div><div>'
  +vs.map(function(v){var t=S.metric==='rate'?v/vmax:Math.sqrt(v/vmax);return '<span class="sw" style="background:'+color(.18+.82*t)+'"></span>'+(S.metric==='rate'?pct(v):n0(v))+'&nbsp;';}).join('')+'</div>'
  +(S.metric==='rate'?'<div>Faded = fewer than 3 counted trips</div>':'');
  }
 if(a&&S.sp!=='all'&&exitsSel().length)h+='<div class="lg-long" style="margin-top:4px"><span class="exl"></span>Caught it, left AIS range here (no stop seen)</div>';
 if(r)h+='<div class="lg-long" style="margin-top:4px"><span class="dot"></span>Report spots: fish quoted in reports naming the spot</div>';
 if(l)h+='<div class="lg-long" style="margin-top:4px"><span class="lsq"></span>Landed at: fish brought home to each port (not where caught)</div>';
 h+='<div class="lg-short">'+(anyHex?'Hex = AIS stops &nbsp;':'')+(a&&S.sp!=='all'&&exitsSel().length?'<span class="exl"></span>left range &nbsp;':'')+(r?'<span class="dot"></span>reports &nbsp;':'')+(l?'<span class="lsq"></span>landed':'')+'</div>';
 return h;}

/* ---------- calendar ---------- */
function calendarSvg(){var cal=D.calendar[S.sp],fishMode=S.metric==='fish',idx=fishMode?1:0,W=356,H=86,P={l:30,r:4,t:6,b:16},n=cal.length;
 var bw=(W-P.l-P.r)/n,max=1;cal.forEach(function(c){if(c[idx]>max)max=c[idx];});
 var y=function(v){return P.t+(H-P.t-P.b)*(1-v/max);};
 var s='<svg class="cal" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Weekly '+(fishMode?'fish':'trips')+' for '+esc(spLabel())+'">';
 s+='<line class="gl" x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y(max)+'" y2="'+y(max)+'"/><text class="ax" x="'+(P.l-3)+'" y="'+(y(max)+3)+'" text-anchor="end">'+(max>=1000?Math.round(max/100)/10+'k':max)+'</text>';
 s+='<line class="gl" x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y(0)+'" y2="'+y(0)+'"/><text class="ax" x="'+(P.l-3)+'" y="'+(y(0)+3)+'" text-anchor="end">0</text>';
 var lastMo=-1,lastX=-99,lastW=0;
 cal.forEach(function(c,i){var wk=D.weeks[i],mo=+wk.slice(5,7),x=P.l+i*bw;
  if(mo!==lastMo){var lbl=MON[mo-1]+(mo===1||lastMo<0?' ’'+wk.slice(2,4):'');
   if((mo%3===1||lastMo<0)&&x-lastX>=lastW+6){s+='<text class="ax" x="'+x+'" y="'+(H-4)+'">'+lbl+'</text>';lastX=x;lastW=lbl.length*5;}lastMo=mo;}
  var on=monthOn(wk),h=c[idx]?Math.max(2,y(0)-y(c[idx])):0;
  s+='<rect x="'+(x+.5).toFixed(2)+'" y="'+(y(0)-h).toFixed(2)+'" width="'+Math.max(1,bw-1).toFixed(2)+'" height="'+h.toFixed(2)+'" rx="1" fill="'+(on?'#fbbf24':'#475569')+'"/>';
  s+='<rect data-i="'+i+'" x="'+x.toFixed(2)+'" y="'+P.t+'" width="'+bw.toFixed(2)+'" height="'+(H-P.t-P.b)+'" fill="transparent" class="hit"/>';});
 return s+'</svg>';}
function wireCalendar(){var tip=document.getElementById('tip');
 document.querySelectorAll('.cal .hit').forEach(function(r){r.addEventListener('mousemove',function(e){var i=+r.getAttribute('data-i'),c=D.calendar[S.sp][i];
  tip.innerHTML='Week of '+fmtDate(D.weeks[i])+'<br/><b>'+n0(c[1])+'</b> '+esc(spLabel())+' on '+c[0]+' of '+D.week_total[i]+' posted trips';
  tip.style.display='block';tip.style.left=Math.min(window.innerWidth-240,e.clientX+12)+'px';tip.style.top=(e.clientY-40)+'px';});
  r.addEventListener('mouseleave',function(){tip.style.display='none';});});}

/* ---------- tables ---------- */
var CMP=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function compass(b){return CMP[Math.floor((b+11.25)/22.5)%16];}
function bearing(a,b){var r=Math.PI/180,y=Math.sin((b[1]-a[1])*r)*Math.cos(b[0]*r),x=Math.cos(a[0]*r)*Math.sin(b[0]*r)-Math.sin(a[0]*r)*Math.cos(b[0]*r)*Math.cos((b[1]-a[1])*r);return (Math.atan2(y,x)/r+360)%360;}
function exitsSel(){var ll=D.meta.landing_ll||{};return (D.exits||[]).filter(function(x){return recHas(x)&&monthOn(x.d.slice(0,7));}).map(function(x){var o=ll[x.l]||[32.72,-117.23];x.brg=bearing(o,x.far);return x;});}

function sortKey(x){return S.sort==='trips'?x.pos:S.sort.charAt(0)==='y'?(x.fy[S.sort.slice(1)]||0):x.fish;}
function headers(first,countLbl){var cols=[['fish','Fish']].concat(YEARS.map(function(y){return ['y'+y,y];})).concat([['trips',countLbl]]);
 return '<tr><th>'+first+'</th>'+cols.map(function(c){return '<th class="n sortable'+(S.sort===c[0]?' on':'')+'" data-sort="'+c[0]+'" title="Sort by '+c[1]+'">'+c[1]+(S.sort===c[0]?' ▾':'')+'</th>';}).join('')+'</tr>';}
function rowCells(x,tilde){return '<td class="n"><b>'+(tilde?'~':'')+n0(x.fish)+'</b></td>'+YEARS.map(function(y){return '<td class="n">'+(x.fy[y]?(tilde?'~':'')+n0(x.fy[y]):'<span class="mut">·</span>')+'</td>';}).join('')+'<td class="n">'+x.pos+'</td>';}

function whereTable(){var rows=[];
 if(S.src==='all'||S.src==='ais')Object.keys(AGG.cells).forEach(function(h){var c=AGG.cells[h];if(c.fish>0)rows.push({kind:'cell',id:h,name:nearTxt(h),src:'AIS stops',x:c,tilde:true});});
 if(S.src==='all'||S.src==='rep')Object.keys(AGG.spots).forEach(function(n){var s=AGG.spots[n];if(s.pos>0)rows.push({kind:'spot',id:n,name:esc(n)+(s.approx?' <span class="mut">≈</span>':''),src:'reports',x:s,tilde:false});});
 rows.sort(function(a,b){return sortKey(b.x)-sortKey(a.x);});
 if(!rows.length)return '<div class="empty">Nothing places '+esc(spLabel())+' at sea for this selection.'+(S.sp==='wahoo'||S.sp==='marlin'||S.sp==='skipjack'?' Almost all of these catches came July to October 2026, when there are no public AIS tracks yet, and no report names a spot for them. Where they were landed is below.':'')+'</div>';
 var h='<table class="sorttab">'+headers('Where','Trips / rpts');
 rows.slice(0,15).forEach(function(r){h+='<tr class="row" data-'+r.kind+'="'+esc(r.id)+'"><td>'+r.name+'<br/><span class="mut">'+r.src+(S.sp==='all'&&r.x.fish?' · '+mixTxt(r.x.mix,2):'')+'</span></td>'+rowCells(r.x,r.tilde)+'</tr>';});
 return h+'</table><div class="mut" style="font-size:11px">'+(rows.length>15?'Top 15 of '+rows.length+'. ':'')+'~ = AIS fish spread over each trip\'s stops by time stopped. Report fish = the number quoted in the sentence.</div>';}
function landTable(){var ld=AGG.lands,rows=Object.keys(ld).filter(function(l){return ld[l].fish>0;}).sort(function(a,b){return sortKey(ld[b])-sortKey(ld[a]);});
 if(!rows.length)return '<div class="empty">No posted counts for this selection.</div>';
 var h='<table class="sorttab">'+headers('Landing','Trips');
 rows.forEach(function(l){var x=ld[l];h+='<tr class="row" data-land="'+esc(l)+'"><td>'+esc(short(l))+'<br/><span class="mut">'+esc(portOf(l))+(S.sp==='all'?' · '+mixTxt(x.mix,2):'')+'</span></td>'+rowCells(x,false)+'</tr>';});
 return h+'</table>';}
function ttLine(){var tt={},n=0,T=D.ttypes[S.sp]||{};Object.keys(T).forEach(function(mo){if(!monthOn(mo))return;Object.keys(T[mo]).forEach(function(b){tt[b]=(tt[b]||0)+T[mo][b];n+=T[mo][b];});});
 if(!n)return '';return '<div class="mut" style="font-size:11px;margin:4px 0">Caught on: '+Object.keys(tt).sort(function(a,b){return tt[b]-tt[a];}).slice(0,5).map(function(b){return esc(b)+' trips '+pct(tt[b]/n);}).join(' · ')+'</div>';}

/* ---------- panels ---------- */
function selCounts(){var pos=0,fish=0;D.weeks.forEach(function(w,i){if(!monthOn(w))return;pos+=D.calendar[S.sp][i][0];fish+=D.calendar[S.sp][i][1];});
 var loc=0;D.trips.forEach(function(t){if(recHas(t)&&monthOn(t.d.slice(0,7)))loc++;});
 var unl=sumMonths(D.unlocated[S.sp]||{}).t,rep=0;D.reports.forEach(function(r){if(r.at&&r.at[S.sp]&&monthOn(r.d.slice(0,7)))rep++;});
 return {pos:pos,fish:fish,loc:loc,unl:unl,rep:rep};}

function overview(){var c=selCounts(),title=S.sp==='all'?'All target species':CATS[S.sp].label;
 var h='<h3>'+esc(title)+'</h3><div class="mut">'+esc(selLabel())+' · dock counts '+fmtDate(D.meta.counts_window[0])+' – '+fmtDate(D.meta.counts_window[1])+'</div>'
  +'<div class="stats"><div class="stat"><b>'+n0(c.fish)+'</b><span>fish on posted counts (all boats)</span></div>'
  +'<div class="stat"><b>'+n0(c.pos)+'</b><span>posted trips that caught '+(S.sp==='all'?'one':'it')+'</span></div>'
  +'<div class="stat"><b>'+n0(c.loc)+'</b><span>trips placed at sea by AIS stops'+(c.unl?' · '+c.unl+' left range first':'')+'</span></div>'
  +'<div class="stat"><b>'+n0(c.rep)+'</b><span>report sentences tie it to a named spot</span></div></div>'
  +'<h4>When: '+(S.metric==='fish'?'fish':'trips')+' per week, every boat</h4>'+calendarSvg()
  +'<div class="mut" style="font-size:11px">Amber = the months selected.</div>'
  +'<h4>Where they caught it <span class="mut">· tap a column to sort</span></h4>'+whereTable();
 var ex=exitsSel();
 if(ex.length&&(S.src==='all'||S.src==='ais')){var sec={};ex.forEach(function(x){var k=compass(x.brg);sec[k]=sec[k]||{n:0,f:0,d:[]};sec[k].n++;sec[k].f+=recFish(x);sec[k].d.push(x.nm);});
  h+='<h4>Caught past AIS range</h4><div class="mut" style="font-size:11px;margin-bottom:4px">Trips that left the shore receivers\' range before a stop was seen: direction from the landing to the farthest point heard (green arrows).</div><table><tr><th>Heading out</th><th class="n">Fish</th><th class="n">Trips</th><th class="n">Seen to (nm)</th></tr>';
  Object.keys(sec).sort(function(a,b){return sec[b].f-sec[a].f;}).slice(0,6).forEach(function(k){var v=sec[k].d.slice().sort(function(a,b){return a-b;});h+='<tr><td>'+k+'</td><td class="n">'+n0(sec[k].f)+'</td><td class="n">'+sec[k].n+'</td><td class="n">'+v[Math.floor(v.length/2)]+'</td></tr>';});
  h+='</table>';}
 if(S.src==='all'||S.src==='land')h+='<h4>Where it was landed <span class="mut">· every posted count</span></h4>'+ttLine()+landTable();
 h+='<div class="caveat"><b>How to read this.</b> Fish totals come from the landings\' posted dock counts. <b>AIS stops</b>: counts joined to the boat\'s tracked trip (same port, boat, return date, and a trip length that fits the posted trip type), with the fish spread over the offshore stops AIS saw. AIS here is shore receivers only, and the public archive ends '+fmtDate(D.meta.ais_window[1])+', so trips past ~50 nm, in Mexican waters, or after that date are not placed. <b>Reports</b>: a landing report sentence naming the species and a charted spot, with the fish quoted in it (reports '+(D.meta.reports_window?fmtDate(D.meta.reports_window[0])+' – '+fmtDate(D.meta.reports_window[1]):'')+'; they almost only name the Coronado Islands). <b>Landed at</b>: where the fish came home, which is all the public data says this year for wahoo and marlin. Dorado and mahi-mahi are the same fish.</div>';
 return h;}

function cellPanel(hx){var a=AGG.cells[hx],c=D.cells[hx];
 var trips=D.trips.filter(function(t){return recHas(t)&&monthOn(t.d.slice(0,7))&&t.cells.indexOf(hx)>=0;}).sort(function(x,y){return x.d<y.d?1:-1;});
 var h='<div class="navrow"><button class="chip" data-back="1">‹ Back</button></div><h3>'+nearTxt(hx)+'</h3>'
  +'<div class="mut">'+c.c[0].toFixed(3)+', '+c.c[1].toFixed(3)+' · AIS stops · '+esc(selLabel())+'</div>'
  +'<div class="stats"><div class="stat"><b>~'+n0(a.fish)+'</b><span>'+esc(spLabel())+' (by time stopped here)</span></div><div class="stat"><b>'+a.pos+' / '+a.tot+'</b><span>counted trips that stopped here caught '+(S.sp==='all'?'one':'it')+'</span></div></div>'
  +(S.sp==='all'&&a.fish?'<div class="mut">'+mixTxt(a.mix,9)+'</div>':'')
  +'<h4>The trips <span class="mut">· tap one to draw its path</span></h4><table><tr><th>Left</th><th>Boat</th><th>Trip</th><th class="n">Fish</th><th class="n">Anglers</th></tr>';
 trips.forEach(function(t){var sel=S.trip&&S.trip[0]===t.m&&S.trip[1]===t.id;
  h+='<tr class="row'+(sel?' sel':'')+'" data-trip="'+t.m+'.'+t.id+'"><td>'+fmtDate(t.d)+'</td><td>'+esc(t.b)+'<br/><span class="mut">'+esc(short(t.l))+'</span></td><td>'+esc(t.tt)+'<br/><span class="mut">'+t.h+' h · '+t.nm+' nm</span></td><td class="n">'+n0(recFish(t))+'</td><td class="n">'+t.a+'</td></tr>';});
 return h+'</table><div class="caveat">Fish are the landing\'s posted total for the whole trip; a trip that stopped in several areas is listed in each.</div>';}

function spotPanel(n){var a=AGG.spots[n];
 var reps=D.reports.filter(function(r){return r.at&&r.at[S.sp]&&r.at[S.sp].indexOf(n)>=0&&monthOn(r.d.slice(0,7));}).sort(function(x,y){return x.d<y.d?1:-1;});
 var h='<div class="navrow"><button class="chip" data-back="1">‹ Back</button></div><h3>'+esc(n)+'</h3>'
  +'<div class="mut">'+(a.approx?'approximate position · ':'chart position · ')+'landing reports · '+esc(selLabel())+'</div>'
  +'<div class="stats"><div class="stat"><b>'+n0(a.fish)+'</b><span>'+esc(spLabel())+' quoted in those sentences</span></div><div class="stat"><b>'+a.pos+' / '+a.tot+'</b><span>reports naming it tie the species to it</span></div></div>'
  +(S.sp==='all'&&a.fish?'<div class="mut">'+mixTxt(a.mix,9)+'</div>':'')+'<h4>The reports</h4><table><tr><th>Date</th><th>Report</th></tr>';
 reps.forEach(function(r){var f=recFish(r);h+='<tr><td>'+fmtDate(r.d)+'</td><td><a href="'+esc(r.u)+'" target="_blank" rel="noopener">'+esc(r.t)+'</a><br/><span class="mut">'+esc(r.a)+(f?' · '+n0(f)+' quoted':'')+'</span></td></tr>';});
 return h+'</table>';}

function landPanel(pn){var x=AGG.ports[pn],LM={};
 x.landings.forEach(function(l){var mm=(D.landed[S.sp]||{})[l]||{};Object.keys(mm).forEach(function(mo){var v=LM[mo]=LM[mo]||[0,0];v[0]+=mm[mo][0];v[1]+=mm[mo][1];});});
 var h='<div class="navrow"><button class="chip" data-back="1">‹ Back</button></div><h3>'+esc(pn)+' landings</h3><div class="mut">'+esc(selLabel())+' · where the fish came home, not where they were caught</div>'
  +'<div class="stats"><div class="stat"><b>'+n0(x.fish)+'</b><span>'+esc(spLabel())+'</span></div><div class="stat"><b>'+n0(x.pos)+'</b><span>posted trips</span></div></div>'
  +(S.sp==='all'?'<div class="mut">'+mixTxt(x.mix,9)+'</div>':'');
 h+='<h4>By landing</h4><table class="sorttab">'+headers('Landing','Trips');
 x.landings.forEach(function(l){h+='<tr><td>'+esc(short(l))+'</td>'+rowCells(AGG.lands[l],false)+'</tr>';});
 h+='</table><h4>By month</h4><table><tr><th>Month</th><th class="n">Fish</th><th class="n">Trips</th></tr>';
 Object.keys(LM).sort().reverse().forEach(function(mo){if(!monthOn(mo)||!LM[mo][1])return;h+='<tr><td>'+MON[+mo.slice(5,7)-1]+' '+mo.slice(0,4)+'</td><td class="n">'+n0(LM[mo][1])+'</td><td class="n">'+LM[mo][0]+'</td></tr>';});
 return h+'</table>';}

function exitPanel(x){return '<div class="navrow"><button class="chip" data-back="1">‹ Back</button></div><h3>'+esc(x.b)+' · '+fmtDate(x.d)+'</h3>'
 +'<div class="mut">'+esc(x.l)+' · '+esc(x.tt)+' · '+x.h+' h</div><div class="stats"><div class="stat"><b>'+n0(recFish(x))+'</b><span>'+esc(spLabel())+' on the dock count ('+x.a+' anglers)</span></div><div class="stat"><b>'+x.nm+' nm</b><span>farthest AIS heard, heading '+compass(x.brg)+'</span></div></div>'
 +'<div class="caveat">The green line is everything AIS heard of this trip ('+Math.round(x.cov*100)+'% of its hours). The fish came from beyond what AIS can see.</div>';}

function renderPanel(){var p=document.getElementById('panel');var html;
 if(S.exitSel&&S.trip&&S.trip[1]===S.exitSel.id)html=exitPanel(S.exitSel);
 else if(S.cell&&AGG.cells[S.cell])html=cellPanel(S.cell);
 else if(S.spot&&AGG.spots[S.spot])html=spotPanel(S.spot);
 else if(S.land&&AGG.ports[S.land])html=landPanel(S.land);
 else html=overview();
 p.innerHTML=html;wireCalendar();
 p.querySelectorAll('[data-sort]').forEach(function(el){el.onclick=function(){S.sort=el.getAttribute('data-sort');commit();};});
 p.querySelectorAll('[data-cell]').forEach(function(el){el.onclick=function(){selectOnly('cell',el.getAttribute('data-cell'));var c=D.cells[S.cell];if(c)pan(c.c);};});
 p.querySelectorAll('[data-spot]').forEach(function(el){el.onclick=function(){selectOnly('spot',el.getAttribute('data-spot'));var s=D.rspots[S.spot];if(s)pan(s.ll);};});
 p.querySelectorAll('[data-land]').forEach(function(el){el.onclick=function(){selectOnly('land',portOf(el.getAttribute('data-land')));var q=AGG.ports[S.land];if(q)pan(q.ll);};});
 p.querySelectorAll('[data-trip]').forEach(function(el){el.onclick=function(){var k=el.getAttribute('data-trip').split('.');S.trip=[+k[0],+k[1]];drawTrip();renderPanel();};});
 p.querySelectorAll('[data-back]').forEach(function(el){el.onclick=function(){selectOnly('none',null);};});}

/* ---------- one trip's path, from Fleet Tracks' per-boat trip files ---------- */
function pan(ll){var sz=map.getSize();if(sz.x>0&&sz.y>0)map.panTo(ll,{animate:false});}
function drawTrip(){pathLayer.clearLayers();if(!S.trip)return;var m=S.trip[0],id=S.trip[1];
 var go=function(j){var t=(j&&j.trips||[]).filter(function(x){return x.id===id;})[0];if(!t||t.pts.length<2)return;
  var ll=t.pts.map(function(p){return [p[1],p[2]];});
  L.polyline(ll,{color:'#0b1220',weight:6,opacity:.6,interactive:false}).addTo(pathLayer);
  L.polyline(ll,{color:'#4ade80',weight:3,opacity:.95,interactive:false}).addTo(pathLayer);
  L.circleMarker(ll[0],{radius:5,color:'#0b1220',weight:2,fillColor:'#4ade80',fillOpacity:1}).addTo(pathLayer);
  var sz=map.getSize();if(sz.x>0&&sz.y>0)map.fitBounds(ll,{animate:false,padding:[30,30],maxZoom:10,paddingBottomRight:[window.innerWidth>760?400:0,window.innerWidth>760?0:Math.round(window.innerHeight*.48)]});};
 if(PATHS[m])return go(PATHS[m]);
 fetch('/fleet/trips/'+m+'.json',{cache:'no-cache'}).then(function(r){return r.ok?r.json():null;}).then(function(j){PATHS[m]=j;go(j);}).catch(function(){});}

/* ---------- URL state + controls ---------- */
function stateToHash(){var p=['sp='+S.sp];if(S.months.length<12)p.push('m='+S.months.join('.'));if(S.year!=='all')p.push('y='+S.year);
 if(S.src!=='all')p.push('src='+S.src);if(S.metric!=='fish')p.push('by='+S.metric);if(S.sort!=='fish')p.push('sort='+S.sort);
 if(S.cell)p.push('cell='+S.cell);if(S.spot)p.push('spot='+encodeURIComponent(S.spot));if(S.land)p.push('land='+encodeURIComponent(S.land));return '#'+p.join('&');}
function readHash(){var q={};location.hash.replace(/^#/,'').split('&').forEach(function(kv){if(!kv)return;var i=kv.indexOf('=');q[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1));});
 S.sp=(q.sp==='all'||CATS[q.sp])?q.sp:'all';S.months=q.m?q.m.split('.').map(Number).filter(function(x){return x>=1&&x<=12;}):[1,2,3,4,5,6,7,8,9,10,11,12];if(!S.months.length)S.months=[1,2,3,4,5,6,7,8,9,10,11,12];
 S.year=q.y||'all';S.src=/^(all|ais|rep|land)$/.test(q.src||'')?q.src:'all';S.metric=/^(fish|trips|rate)$/.test(q.by||'')?q.by:'fish';
 S.sort=/^(fish|trips|y\d{4})$/.test(q.sort||'')?q.sort:'fish';
 S.cell=q.cell&&D.cells[q.cell]?q.cell:null;S.spot=q.spot&&D.rspots[q.spot]?q.spot:null;var PN={};Object.keys(D.meta.landing_port||{}).forEach(function(l){PN[D.meta.landing_port[l]]=1;});S.land=q.land&&PN[q.land]?q.land:null;S.trip=null;S.exitSel=null;}
function commit(){var h=stateToHash();if(h!==location.hash)history.pushState(null,'',location.pathname+h);sync();draw();}
function sync(){document.querySelectorAll('#species .chip').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-k')===S.sp);});
 document.querySelectorAll('#months .chip').forEach(function(b){b.classList.toggle('on',S.months.indexOf(+b.getAttribute('data-m'))>=0);});
 document.querySelectorAll('#seasons .chip').forEach(function(b){var m=JSON.parse(b.getAttribute('data-ms'));b.classList.toggle('on',m.length===S.months.length&&m.every(function(x){return S.months.indexOf(x)>=0;}));});
 document.getElementById('year').value=S.year;document.getElementById('src').value=S.src;document.getElementById('metric').value=S.metric;}

function boot(d){D=d;D.cats.forEach(function(c){CATS[c.key]=c;});
 var ys={};D.months.forEach(function(m){ys[m.slice(0,4)]=1;});YEARS=Object.keys(ys).sort();
 var last=D.meta.counts_window[1].slice(0,7),ly=+last.slice(0,4),lm=+last.slice(5,7)-11;if(lm<1){lm+=12;ly--;}LAST12=[ly+'-'+(lm<10?'0':'')+lm,last];
 map=L.map('map',{zoomControl:true,attributionControl:false}).setView([32.9,-118.6],8);
 // fit the SoCal bight once the map has a real size (a hidden tab boots at 0x0)
 var home=function(){var sz=map.getSize();if(sz.x<200||sz.y<200)return false;
  var top=document.getElementById('bar').offsetHeight,wide=window.innerWidth>760;
  map.fitBounds([[31.6,-120.2],[34.2,-117.0]],{animate:false,paddingTopLeft:[0,top],paddingBottomRight:[wide?400:0,wide?0:Math.round(window.innerHeight*.46)]});return true;};
 if(!home()){var once=function(){map.invalidateSize();if(home())window.removeEventListener('resize',once);};window.addEventListener('resize',once);}
 var land=L.layerGroup().addTo(map);
 fetch(LAND_URL).then(function(r){return r.ok?r.json():null;}).then(function(g){if(g)L.geoJSON(g,{style:{color:'#475569',weight:.6,fillColor:'#1f2937',fillOpacity:1},interactive:false}).addTo(land);}).catch(function(){});
 hexLayer=L.layerGroup().addTo(map);landLayer=L.layerGroup().addTo(map);lblLayer=L.layerGroup().addTo(map);map.on('zoomend',placeLandLabels);spotLayer=L.layerGroup().addTo(map);pathLayer=L.layerGroup().addTo(map);
 var sp=document.getElementById('species');
 [{key:'all',label:'All species'}].concat(D.cats).forEach(function(c){var b=document.createElement('button');b.className='chip';b.setAttribute('data-k',c.key);
  var s=D.summary[c.key]||{fish:0,posted_trips:0};b.textContent=c.label+' · '+n0(s.fish);b.title=n0(s.fish)+' fish on '+n0(s.posted_trips)+' posted trips';
  b.onclick=function(){S.sp=c.key;selectOnly('none',null);};sp.appendChild(b);});
 var ms=document.getElementById('months');MON.forEach(function(m,i){var b=document.createElement('button');b.className='chip';b.setAttribute('data-m',i+1);b.textContent=m;
  b.onclick=function(){var k=S.months.indexOf(i+1);if(S.months.length===12){S.months=[i+1];}else if(k>=0){S.months.splice(k,1);if(!S.months.length)S.months=[1,2,3,4,5,6,7,8,9,10,11,12];}else S.months.push(i+1);commit();};ms.appendChild(b);});
 var ss=document.getElementById('seasons');SEASONS.forEach(function(s){var b=document.createElement('button');b.className='chip';b.setAttribute('data-ms',JSON.stringify(s[1]));b.textContent=s[0];b.onclick=function(){S.months=s[1].slice();commit();};ss.appendChild(b);});
 var yr=document.getElementById('year');[['all','Both years'],['12m','Last 12 months']].concat(YEARS.map(function(y){return [y,y];})).forEach(function(o){var e=document.createElement('option');e.value=o[0];e.textContent=o[1];yr.appendChild(e);});
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
