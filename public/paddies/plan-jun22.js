(function(){
  var MB=[32.77,-117.25];
  var WPS=[[32.96,-117.60,"1 · off Del Mar ~10nm"],[33.04,-117.76,"2 · off Carlsbad ~16nm"],[33.20,-117.76,"3 · off Oceanside ~11nm"],[33.32,-117.96,"4 · off Pendleton ~17nm"]];
  function go(){
    if(typeof map==="undefined"||!map||typeof L==="undefined"||!window.D||!map._loaded){return setTimeout(go,120);}
    try{
      ["spotLayer","bankLayer","portLayer"].forEach(function(n){if(window[n]&&map.hasLayer(window[n]))map.removeLayer(window[n]);});
      var pn=document.getElementById("panel"); if(pn)pn.style.display="none";
      var plan=L.layerGroup().addTo(map);
      L.polyline([MB].concat(WPS.map(function(w){return [w[0],w[1]];})),{color:"#ffffff",weight:3,dashArray:"9 7",opacity:.95}).addTo(plan);
      L.polyline([[33.16,-117.86],[32.99,-117.63]],{color:"#ffd56b",weight:4,opacity:.9}).addTo(plan);
      L.marker([32.99,-117.63],{interactive:false,icon:L.divIcon({className:"",html:"<div style='color:#ffd56b;font:600 11px system-ui;white-space:nowrap;text-shadow:0 0 3px #000'>↘ drift SE ~18nm</div>",iconSize:[120,16],iconAnchor:[-6,8]})}).addTo(plan);
      [[32.77,-117.67,"14-Mile Bank"],[32.62,-117.42,"9-Mile Bank"]].forEach(function(b){L.marker([b[0],b[1]],{interactive:false,icon:L.divIcon({className:"",html:"<div style='color:#c8d2dc;font:10px system-ui;text-shadow:0 0 3px #000;white-space:nowrap'>⊕ "+b[2]+"</div>",iconSize:[90,14],iconAnchor:[6,7]})}).addTo(plan);});
      L.circleMarker(MB,{radius:7,color:"#0b1220",weight:2,fillColor:"#f6c244",fillOpacity:1}).bindTooltip("Mission Bay (launch)",{permanent:true,direction:"left",offset:[-8,0],className:"rep-tip"}).addTo(plan);
      WPS.forEach(function(w){L.circleMarker([w[0],w[1]],{radius:8,color:"#ffffff",weight:2,fillColor:"#22c55e",fillOpacity:.95}).bindTooltip(w[2],{permanent:true,direction:"right",offset:[9,0],className:"rep-tip"}).addTo(plan);});
      map.fitBounds([[32.62,-118.05],[33.40,-117.15]],{padding:[26,26]});
      setTimeout(function(){try{map.invalidateSize();}catch(e){}},150);
    }catch(e){}
  }
  go();
})();
