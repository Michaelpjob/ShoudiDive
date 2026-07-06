"""Render outputs.

The unified dashboard (write_dashboard) + static overview (write_overview)
show: the opportunity field (faint underlay), kelp drift CONES (direction),
and ranked convergence/opportunity ZONES (where kelp + convergence + temp
breaks coincide) — replacing the old random hotspot pins. The
single-scenario CLI helpers (write_all etc., used by run.py) emit data only.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os

import numpy as np
from PIL import Image

import config
import landmask as landmask_mod

_BAND_COLOR = {"Minimal": "#64748b", "Low": "#0ea5e9", "Moderate": "#eab308",
               "High": "#f97316", "Extreme": "#dc2626"}


def _fc(records):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
         "properties": {k: v for k, v in r.items() if k not in ("lng", "lat")}}
        for r in records]}


def _polys(geom):
    t = (geom or {}).get("type")
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return geom["coordinates"]
    return []


def write_geojson(result, hotspots, outdir):
    for key in ("floating", "beached", "sunk"):
        with open(os.path.join(outdir, f"{key}.geojson"), "w") as f:
            json.dump(_fc(result[key]), f)
    with open(os.path.join(outdir, "meta.json"), "w") as f:
        json.dump(result["meta"], f, indent=2)


def write_all(result, dens, hotspots, launch_name, launch_ll, outdir):
    os.makedirs(outdir, exist_ok=True)
    write_geojson(result, hotspots, outdir)
    with open(os.path.join(outdir, "hotspots.json"), "w") as f:
        json.dump(hotspots, f, indent=2)
    return None


# ===========================================================================
# Convergence / opportunity dashboard
# ===========================================================================
def opp_png_b64(opp_data):
    """Bright per-scenario kelp-density gradient (inferno) — the smooth
    'where is the kelp concentrated' field that replaces scattered dots."""
    from matplotlib import colormaps
    opp = opp_data["opp"]
    peak = opp.max() or 1.0
    norm = np.clip(opp / peak, 0, 1) ** 0.5      # per-scenario, sqrt for readability
    rgba = (colormaps["inferno"](norm) * 255).astype(np.uint8)
    rgba[..., 3] = (np.clip(norm * 1.5, 0, 1) * 215).astype(np.uint8)
    rgba[opp <= 0, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def prob_dots(opp_data, n=1300):
    """Sample n dots from the probability field — denser where kelp is more
    probable, spread to the low-probability edges. Each dot carries its
    normalized probability (1 = green / most probable, 0 = red / least)."""
    g = opp_data["opp"]
    lats, lngs = opp_data["lats"], opp_data["lngs"]
    H, W = g.shape
    peak = float(g.max()) or 1.0
    flat = np.clip(g / peak, 0, 1).flatten()
    samp = flat ** 1.4                          # concentrate dots on the likely areas
    s = samp.sum()
    if s <= 0:
        return []
    rng = np.random.default_rng(config.SEED)
    idx = rng.choice(flat.size, size=n, p=samp / s)
    jit = (rng.random((n, 2)) - 0.5) * config.DENSITY_STEP_DEG
    out = []
    for t, k in enumerate(idx):
        j, i = divmod(int(k), W)
        out.append([round(float(lngs[i] + jit[t, 1]), 3),
                    round(float(lats[j] + jit[t, 0]), 3),
                    round(float(flat[k]), 2)])
    return out


DASH_HTML = """<!doctype html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SCB Kelp-Paddy Finder</title>
__LEAFLET__
<style>
 html,body{height:100%;margin:0;font-family:system-ui;background:#0b1220}
 #map{position:absolute;inset:0}
 .top{position:absolute;z-index:1000;top:10px;left:10px;background:rgba(15,23,42,.93);color:#e2e8f0;
  padding:8px 10px;border-radius:9px;font-size:13px}
 .tabs{display:inline-flex;gap:4px;margin-left:6px}
 .tabs button{background:#1e293b;color:#cbd5e1;border:1px solid #334155;border-radius:6px;
  padding:3px 8px;cursor:pointer;font-size:12px}
 .tabs button.active{background:#f59e0b;color:#0b1220;font-weight:700;border-color:#f59e0b}
 .time{display:inline-flex;align-items:center;gap:8px;margin-left:8px}
 .time input[type=range]{width:150px;accent-color:#f59e0b;cursor:pointer;vertical-align:middle}
 #tlabel{font-size:12px;min-width:118px;display:inline-block}
 #tlabel b{color:#fbbf24}
 .conf{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;margin-left:5px}
 .conf.obs{background:#14532d;color:#86efac} .conf.fc{background:#7c2d12;color:#fdba74}
 select{background:#1e293b;color:#cbd5e1;border:1px solid #334155;border-radius:6px;padding:3px 6px}
 .panel{position:absolute;z-index:1000;top:10px;right:10px;width:300px;background:rgba(15,23,42,.93);
  color:#e2e8f0;padding:12px 14px;border-radius:9px;font-size:12px;line-height:1.45}
 .score{font-size:24px;font-weight:700;line-height:1}
 .band{display:inline-block;padding:2px 8px;border-radius:6px;color:#0b1220;font-weight:700;font-size:11px}
 .bar{height:8px;border-radius:4px;display:flex;overflow:hidden;margin:6px 0}
 .hs{margin:3px 0} .mut{color:#94a3b8}
 .zpin{background:#fde047;color:#0b1220;border-radius:50%;width:22px;height:22px;font:700 13px system-ui;
  text-align:center;line-height:22px;border:2px solid #0b1220}
 .ruler{color:#38bdf8;font-size:21px;line-height:24px;text-align:center;cursor:grab;font-weight:700;
  text-shadow:0 0 4px #000,0 0 2px #000}
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
 .panel-head .chev{display:none}
 @media (max-width:680px),(hover:none) and (pointer:coarse){
  .top{left:6px;right:6px}
  .coordbox{display:none}
  .panel{top:auto;bottom:6px;left:6px;right:6px;width:auto;max-height:52vh;font-size:12px}
  .panel-head{cursor:pointer;position:relative;padding-right:18px}
  .panel-head .chev{display:block;position:absolute;top:0;right:0;color:#94a3b8;font-size:15px;transition:transform .15s}
  .panel.min .panel-body{display:none}
  .panel.min .panel-head .chev{transform:rotate(-90deg)}
 }
</style></head><body>
<div id="map"></div>
<div class="top"><b>SCB Kelp-Paddy Finder</b>
 <span class="time"><input type="range" id="tslider" min="0" step="1"/><span id="tlabel"></span></span>
 &nbsp;Launch: <select id="launch"></select></div>
<div class="panel" id="panel"></div>
<div class="coordbox" id="coordbox"></div>
<script>
const D=__DATA__, REACH=__REACH__;
const BAND={Minimal:'#64748b',Low:'#0ea5e9',Moderate:'#eab308',High:'#f97316',Extreme:'#dc2626'};
const LABEL={live:'Live',storm:'Storm 3d ago',swell:'+2m Swell',warm:'+4\\u00b0C Warm'};
let F=D.default_frame, LCH=D.default_launch;
const map=L.map('map').setView([33.2,-118.5],8);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap',maxZoom:12}).addTo(map);
const coneLayer=L.layerGroup().addTo(map);
const hdrLayer=L.layerGroup().addTo(map);  // dim->bright green
L.geoJSON(D.beds,{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:f.properties.island?5:3,
 color:'#052e16',weight:1,fillColor:f.properties.island?'#16a34a':'#4d7c4d',fillOpacity:1})
 .bindTooltip(f.properties.bed)}).addTo(map);
const launchMarker=L.circleMarker(D.launches[LCH],{radius:6,color:'#0b1220',weight:2,fillColor:'#facc15',fillOpacity:1}).addTo(map);
const toR=x=>x*Math.PI/180,toD=x=>x*180/Math.PI;
function nm(a,b){const R=6371,dp=toR(b[0]-a[0]),dl=toR(b[1]-a[1]);
 const h=Math.sin(dp/2)**2+Math.cos(toR(a[0]))*Math.cos(toR(b[0]))*Math.sin(dl/2)**2;
 return 2*R*Math.asin(Math.min(1,Math.sqrt(h)))/1.852;}
function brg(a,b){const y=Math.sin(toR(b[1]-a[1]))*Math.cos(toR(b[0]));
 const x=Math.cos(toR(a[0]))*Math.sin(toR(b[0]))-Math.sin(toR(a[0]))*Math.cos(toR(b[0]))*Math.cos(toR(b[1]-a[1]));
 return (toD(Math.atan2(y,x))+360)%360;}
const CMP=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function comp(b){return CMP[Math.floor((b+11.25)/22.5)%16];}
// --- GPS coordinate readout + click-to-drop waypoint ---
function _ddm(v,p,n){const h=v>=0?p:n;v=Math.abs(v);const d=Math.floor(v);return d+'\\u00b0'+((v-d)*60).toFixed(3)+"' "+h;}
function _fmt(ll){return {dd:ll.lat.toFixed(5)+', '+ll.lng.toFixed(5),dm:_ddm(ll.lat,'N','S')+'  '+_ddm(ll.lng,'E','W')};}
let wpMarker=null;const coordbox=document.getElementById('coordbox');
function setReadout(ll){const f=_fmt(ll);coordbox.innerHTML='<b>'+f.dd+'</b><br/>'+f.dm+'<div class="hint">click map to drop a GPS waypoint</div>';}
function openWp(ll){const f=_fmt(ll);wpMarker.setPopupContent('<div class="wpc"><b>'+f.dd+'</b><br/>'+f.dm+'</div><button class="copybtn" data-c="'+f.dd+'">Copy GPS</button>');wpMarker.openPopup();}
function dropWp(ll){if(!wpMarker){wpMarker=L.marker(ll,{draggable:true,zIndexOffset:1100,icon:L.divIcon({className:'',html:'<div class=wp>\\u2316</div>',iconSize:[24,24],iconAnchor:[12,12]})}).addTo(map);wpMarker.bindPopup('');wpMarker.on('drag',()=>openWp(wpMarker.getLatLng()));}else wpMarker.setLatLng(ll);openWp(ll);}
map.on('mousemove',e=>setReadout(e.latlng));
map.on('click',e=>dropWp(e.latlng));
map.on('popupopen',e=>{const el=e.popup.getElement();if(!el)return;const b=el.querySelector('.copybtn');if(!b||b._wired)return;b._wired=1;b.addEventListener('click',()=>{const t=b.getAttribute('data-c');const done=ok=>{b.textContent=ok?'Copied!':t;};const legacy=()=>{try{const a=document.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity=0;document.body.appendChild(a);a.select();const ok=document.execCommand('copy');document.body.removeChild(a);done(ok);}catch(_){done(false);}};if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(()=>done(true),legacy);else legacy();});});
setReadout(map.getCenter());
panel.classList.add('min');
panel.addEventListener('click',e=>{if(e.target.closest('.panel-head'))panel.classList.toggle('min');});
function drawBase(){coneLayer.clearLayers();
 L.geoJSON({type:'FeatureCollection',features:D.frames[F].cones},{interactive:false,style:f=>({color:'#f59e0b',weight:1,
  opacity:.2+.3*f.properties.opacity,fillColor:'#f59e0b',fillOpacity:.03+.14*f.properties.opacity})}).addTo(coneLayer);}
// --- measuring ruler: a line anchored at your port; drag the handle to read nm ---
function measStart(){return D.launches[LCH];}
let measEnd=[measStart()[0]-0.05,measStart()[1]-0.35];   // ~18 nm offshore default
const measLine=L.polyline([measStart(),measEnd],{color:'#38bdf8',weight:3,dashArray:'7 6',opacity:.9}).addTo(map);
const measHandle=L.marker(measEnd,{draggable:true,zIndexOffset:1000,icon:L.divIcon({className:'',
 html:'<div class=ruler>\\u2295</div>',iconSize:[26,26],iconAnchor:[13,13]})}).addTo(map);
measHandle.bindTooltip('',{permanent:true,direction:'top',offset:[0,-10],className:'meas-label'});
function updateMeas(){const a=measStart(),h=measHandle.getLatLng(),b=[h.lat,h.lng];
 measLine.setLatLngs([a,b]);
 measHandle.setTooltipContent(nm(a,b).toFixed(1)+' nm '+comp(brg(a,b)));}
measHandle.on('drag',updateMeas);
function reanchorMeas(){updateMeas();}
function drawHDR(){hdrLayer.clearLayers();const h=D.frames[F].hdr;if(!h||!h.regions)return;
 h.regions.slice().sort((a,b)=>b.level-a.level).forEach(r=>{
  const core=r.level<=0.35,mid=r.level>0.35&&r.level<=0.6;
  const st=core?{color:'#86efac',weight:2,fillColor:'#22c55e',fillOpacity:.55}
          :mid?{color:'#4ade80',weight:1.5,fillColor:'#16a34a',fillOpacity:.22}
          :{color:'#4ade80',weight:1,fillColor:'#16a34a',fillOpacity:.08,dashArray:'4 4'};
  L.geoJSON(r.fc,{style:st,interactive:false}).addTo(hdrLayer);});}
function drawPanel(){const m=D.frames[F].meta;
 const fl=Math.round(m.frac_floating*100),be=Math.round(m.frac_beached*100),sk=Math.round(m.frac_sunk*100);
 const tl=D.frames[F].timeline||[];
 const tHdr='<div style="margin-bottom:7px;font-size:13px">'+m.date+' &middot; <b style="color:#fbbf24">'+m.rel+'</b>'
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FORECAST':'OBSERVED')+'</span></div>'
  +(m.confidence==='forecast'?'<div class=mut style="font-size:11px;margin:-4px 0 7px">forecast drift &mdash; coarse currents, no radar; lower confidence</div>':'');
 const mx=Math.max.apply(null,[0.1].concat(tl.map(t=>t.shed)));
 const tlb=tl.slice().reverse().map(t=>'<div style="flex:1;text-align:center" title="Hs '+t.hs_m+' m">'
  +'<div style="height:'+Math.round(26*t.shed/mx)+'px;background:#38bdf8;border-radius:2px 2px 0 0"></div>'
  +'<div style="font-size:9px;color:#94a3b8">'+t.days_ago+'</div></div>').join('');
 let best;
 if(m.diffuse){best='<div style="background:#3f2d14;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#fcd34d">Paddies scattered today</b>'
   +'<br/><span class=mut>no tight concentration &mdash; work a wide area &middot; position \\u00b1'+(m.pos_pm_nm||0)+' nm</span></div>';}
 else{best='<div style="background:#14532d;border-radius:7px;padding:7px 9px;margin-bottom:8px">'
   +'<b style="color:#86efac">Densest paddies'+(m.feature?' near '+m.feature:'')+'</b>'
   +'<br/><span class=mut>primary patch ~'+(m.core_area_km2||0)+' km\\u00b2'
   +((m.n_patches>1)?' &middot; +'+(m.n_patches-1)+' more patches':'')
   +' &middot; \\u00b1'+(m.pos_pm_nm||0)+' nm &middot; widen to ~'+(m.area50_km2||0)+' km\\u00b2 if slow</span></div>';}
 panel.innerHTML='<div class="panel-head"><span class="chev">\\u25be</span>'+tHdr+best+'</div><div class="panel-body">'
  +'<div class=mut style="margin:-2px 0 7px">Drag the <b style="color:#38bdf8">\\u2295</b> to measure nm from your port.</div>'
  +'<div class=mut>est. floating paddies in the Bight</div>'
  +'<div class=score>~'+m.est_floating_paddies.toLocaleString()+'</div>'
  +'<div style="margin:5px 0"><span class=band style="background:'+(BAND[m.abundance_band]||'#64748b')
  +'">'+m.abundance_band+' input</span></div>'
  +'<div class=mut style="margin-bottom:4px">kelp drifting <b style="color:#e2e8f0">'+(m.drift_comp||'?')
  +'</b> ~'+(m.drift_nm||0)+' nm from the beds</div>'
  +'<div class=bar><div style="width:'+fl+'%;background:#f97316"></div>'
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
  +' <span class="sw" style="background:#38bdf8"></span>ruler (drag \\u2295)'
  +'<div class=mut style="margin-top:6px;font-size:11px">'+(D.current_note?D.current_note+'<br/>':'')+D.src_note+'</div></div></div>';}
function setFrame(i){F=Math.max(0,Math.min(D.frames.length-1,i|0));const m=D.frames[F].meta;
 document.getElementById('tlabel').innerHTML='<b>'+m.date+'</b> &middot; '+m.rel
  +'<span class="conf '+(m.confidence==='forecast'?'fc':'obs')+'">'+(m.confidence==='forecast'?'FC':'OBS')+'</span>';
 drawBase();drawHDR();drawPanel();}
function setL(l){LCH=l;launchMarker.setLatLng(D.launches[LCH]);reanchorMeas();drawHDR();drawPanel();}
const tsl=document.getElementById('tslider');
tsl.max=D.frames.length-1;tsl.value=D.default_frame;
tsl.oninput=()=>setFrame(parseInt(tsl.value,10));
const sel=document.getElementById('launch');
Object.keys(D.launches).forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o);});
sel.value=LCH;sel.onchange=e=>setL(e.target.value);
setFrame(D.default_frame);
updateMeas();
</script></body></html>"""


def _read_vendor(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read()
    return None


def write_dashboard(data, outdir):
    os.makedirs(outdir, exist_ok=True)
    css, js = _read_vendor("leaflet.css"), _read_vendor("leaflet.js")
    if css and js:
        head = f"<style>{css}</style>\n<script>{js}</script>"
    else:
        head = ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
                '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>')
    html = (DASH_HTML.replace("__LEAFLET__", head)
            .replace("__DATA__", json.dumps(data))
            .replace("__REACH__", str(config.REACHABLE_NM)))
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def write_overview(items, launch_ll, launch_name, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPoly
    from matplotlib.collections import PatchCollection

    b = config.FIELD_BBOX
    land = landmask_mod._load_land()
    landcoords = [poly[0] for feat in (land or {}).get("features", [])
                  for poly in _polys(feat.get("geometry")) if poly and poly[0]]
    n = len(items)
    fig, axes = plt.subplots(1, n, figsize=(5.0 * n, 7.2), facecolor="#0b1220")
    if n == 1:
        axes = [axes]
    LAB = {"live": "LIVE (today)", "storm": "STORM 3d ago", "swell": "+2 m SWELL",
           "warm": "+4°C WARM"}
    for ax, it in zip(axes, items):
        m = it["meta"]
        ax.set_facecolor("#0b1220")
        if landcoords:
            ax.add_collection(PatchCollection([MplPoly(c, closed=True) for c in landcoords],
                              facecolor="#1f2937", edgecolor="#475569", linewidths=.3, zorder=2))
        for f in it["cones"]:
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for poly in polys:
                ax.add_patch(MplPoly(poly[0], closed=True, facecolor="#f59e0b",
                                     alpha=0.06 + 0.32 * f["properties"]["opacity"],
                                     edgecolor="#f59e0b", linewidth=.4, zorder=4))
        hdr = it.get("hdr") or {}
        for r in sorted(hdr.get("regions", []), key=lambda x: -x["level"]):
            core = r["level"] <= 0.35
            mid = 0.35 < r["level"] <= 0.6
            for feat in r["fc"]["features"]:
                gm = feat["geometry"]
                polys = gm["coordinates"] if gm["type"] == "MultiPolygon" else [gm["coordinates"]]
                for poly in polys:
                    ax.add_patch(MplPoly(poly[0], closed=True,
                                         facecolor="#22c55e" if core else "#16a34a",
                                         alpha=.5 if core else (.2 if mid else .08),
                                         edgecolor="#86efac" if core else "#4ade80",
                                         linewidth=2.0 if core else (1.2 if mid else .8),
                                         zorder=7 if core else (6 if mid else 5)))
        ax.scatter([launch_ll[1]], [launch_ll[0]], marker="^", s=90, c="#facc15",
                   edgecolor="#0b1220", linewidths=.6, zorder=8)
        ax.set_xlim(b["lng_min"], b["lng_max"])
        ax.set_ylim(b["lat_min"], b["lat_max"])
        ax.set_aspect(1.0 / math.cos(math.radians(33.0)))
        ax.tick_params(colors="#94a3b8", labelsize=7)
        for sp in ax.spines.values():
            sp.set_color("#475569")
        hd = it.get("hdr") or {}
        regs = hd.get("regions", [])
        prim = hd.get("core_primary_km2", next((r["area_km2"] for r in regs if r["level"] <= 0.35), 0))
        npatch = hd.get("n_core_patches", 1)
        a50 = next((r["area_km2"] for r in regs if 0.45 <= r["level"] <= 0.55), 0)
        patch = f" +{npatch-1} more" if npatch > 1 else ""
        ax.set_title(f"{m.get('rel', LAB.get(m['scenario'], m['scenario']))}\n"
                     f"~{m['est_floating_paddies']:,} floating · {m['abundance_band']} input\n"
                     f"primary patch ~{prim:,} km²{patch} · 50% ~{a50:,} km²",
                     color="#e2e8f0", fontsize=9.5)
    fig.suptitle(f"SCB Kelp-Paddy Finder — densest / 50% / 80% regions  "
                 f"(launch: {launch_name})",
                 color="#e2e8f0", fontsize=13, y=.99)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(outdir, "overview.png")
    fig.savefig(out, dpi=125, facecolor="#0b1220")
    plt.close(fig)
    return out
