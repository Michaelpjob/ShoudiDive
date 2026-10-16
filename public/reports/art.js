/* Drawn fish for the Ocean Report and the fleet log: simple side-on
   silhouettes (64 x 30 viewBox, facing right), one per kind of fish, as
   inline SVG. window.SDArt.fishIcon(name) returns the markup. */
(function () {
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // Simple side-on silhouettes (64 x 30, facing right), one per kind of fish,
  // so a reader sees what is biting before reading a number.
  function svgFish(parts, label) {
    return '<svg class="fi" viewBox="0 0 64 30" role="img" aria-label="' + esc(label) + '">' + parts + "</svg>";
  }
  function eye(cx, cy) { return '<circle cx="' + cx + '" cy="' + cy + '" r="1.4" fill="#0b1220"/><circle cx="' + (cx + 0.3) + '" cy="' + (cy - 0.3) + '" r=".5" fill="#fff"/>'; }
  function tunaArt(back, belly, fin, extra) {
    return '<path fill="' + fin + '" d="M33 8 Q30 2 25 .8 Q28 5 29 9.2Z"/><path fill="' + fin + '" d="M33 22 Q30 28 25 29.2 Q28 25 29 20.8Z"/>' +
      '<path fill="' + back + '" d="M15 14 L3 3 Q9 10 9.5 15 Q9 20 3 27 L15 16Z"/>' +
      '<path fill="' + back + '" d="M61 15 C53 7 37 5 23 10 C18 12 15 13.5 13 15 C15 16.5 18 18 23 20 C37 25 53 23 61 15Z"/>' +
      '<path fill="' + belly + '" d="M61 15 C53 21.5 37 24 23 20 C18 18 15 16.5 13 15 C28 18 46 19 61 15Z"/>' +
      '<path fill="' + back + '" d="M45 7.3 L41 3.2 L37 7.6Z"/><path fill="' + fin + '" opacity=".9" d="M47 15.5 L37 13.2 L46.5 17.2Z"/>' +
      '<g fill="' + fin + '"><circle cx="21" cy="10.8" r=".9"/><circle cx="18.2" cy="12" r=".8"/><circle cx="21" cy="19.2" r=".9"/><circle cx="18.2" cy="18" r=".8"/></g>' +
      (extra || "") + eye(55, 13.6);
  }
  function plainArt(back, belly, fin, slim) {
    var k = slim ? 0.6 : 1, y = function (v) { return (15 + (v - 15) * k).toFixed(1); };
    return '<path fill="' + fin + '" d="M15 14 L5 ' + y(6) + " Q7.5 15 5 " + y(24) + ' L15 16Z"/>' +
      '<path fill="' + back + '" d="M61 15 C54 ' + y(8.5) + " 36 " + y(7.5) + " 22 " + y(11) + " C18 " + y(12) + " 15 " + y(13.5) + " 13 15 C15 " + y(16.5) + " 18 " + y(18) + " 22 " + y(19) + " C36 " + y(22.5) + " 54 " + y(21.5) + ' 61 15Z"/>' +
      '<path fill="' + belly + '" d="M61 15 C54 ' + y(21) + " 36 " + y(22.5) + " 22 " + y(19) + " C18 " + y(18) + " 15 " + y(16.5) + " 13 15 C28 " + y(17.5) + " 46 " + y(18) + ' 61 15Z"/>' +
      '<path fill="' + fin + '" d="M42 ' + y(9) + " L36 " + y(5.5) + " L28 " + y(10) + 'Z"/>' + eye(55, 14);
  }
  var ART_BY = {
    yellowfin: function () { return tunaArt("#1e3a8a", "#cbd5e1", "#facc15"); },
    bluefin: function () { return tunaArt("#172554", "#cbd5e1", "#64748b"); },
    albacore: function () { return tunaArt("#1e3a5f", "#e2e8f0", "#94a3b8", '<path fill="#94a3b8" d="M48 15 L28 12.4 L47 17Z"/>'); },
    skipjack: function () { return tunaArt("#334155", "#e2e8f0", "#64748b", '<g stroke="#475569" stroke-width=".8" fill="none"><path d="M50 18 C40 20 30 20 22 18.5"/><path d="M48 19.8 C40 21.5 30 21.5 24 20"/></g>'); },
    "bullet tuna": function () { return ART_BY.skipjack(); },
    bonito: function () { return tunaArt("#1e40af", "#e2e8f0", "#64748b", '<g stroke="#0f172a" stroke-width=".9"><path d="M52 9.5 L47 13"/><path d="M46 8.5 L41 12.5"/><path d="M40 8.3 L35 12.5"/><path d="M34 9 L29 13"/></g>'); },
    yellowtail: function () {
      return '<path fill="#eab308" d="M15 14 L3 4 Q8 11 9.5 15 Q8 19 3 26 L15 16Z"/>' +
        '<path fill="#2b6cb0" d="M62 15 C55 9 38 8 22 11 C18 12 15 13.5 13 15 C15 16.5 18 18 22 19 C38 22 55 21 62 15Z"/>' +
        '<path fill="#e2e8f0" d="M62 15 C55 20 38 21.5 22 19 C18 18 15 16.5 13 15 C28 16.8 46 17.6 62 15Z"/>' +
        '<path fill="#facc15" d="M60 14.2 C46 12.8 30 13.4 14 14.7 L14 15.5 C30 14.9 46 14.6 60 15.4Z"/>' +
        '<path fill="#2b6cb0" d="M44 9.1 L40 6.6 L36 9.4Z"/><path fill="#eab308" d="M34 9.6 L32 7 L20 11.4 L22 11.8Z"/><path fill="#eab308" d="M34 20.4 L32 22.6 L21 18.8 L23 18.4Z"/>' + eye(56, 13.8);
    },
    dorado: function () {
      return '<path fill="#a3e635" d="M16 14.2 L4 4 Q9 11 10 15 Q9 19 4 26 L16 15.8Z"/>' +
        '<path fill="#65a30d" d="M60 15 C60 9 56 6.5 49 7 C37 8 24 11 14 14.2 L14 15.8 C24 19 37 22.5 48 22.5 C56 22.5 60 19.5 60 15Z"/>' +
        '<path fill="#facc15" d="M60 16 C59 20 55 22.5 48 22.5 C37 22.5 24 19 14 15.8 C28 17.5 44 19.5 60 16Z"/>' +
        '<path fill="#0e7490" d="M57 7.4 C46 2.5 28 6 16 13.2 L19 13.2 C30 8.5 45 6.8 56.5 9Z"/>' +
        '<g fill="#22d3ee"><circle cx="44" cy="12" r=".8"/><circle cx="38" cy="13" r=".8"/><circle cx="32" cy="14.5" r=".7"/><circle cx="46" cy="15.5" r=".7"/></g>' + eye(56, 12.5);
    },
    "calico bass": function () {
      return '<path fill="#57534e" d="M15 15 L5 7.5 Q3.2 15 5 22.5Z"/>' +
        '<path fill="#78716c" d="M61 15.5 C58 10 48 8.5 36 9 C26 9.5 18 12 14 15 C18 18.5 26 21 36 21.5 C48 22 58 20.5 61 15.5Z"/>' +
        '<path fill="#d6d3d1" d="M61 16 C58 20 48 22 36 21.5 C26 21 18 18.5 14 15 C26 17.5 44 18.3 61 16Z"/>' +
        '<path fill="#57534e" d="M46 9.2 L44.5 4.5 L42 8.9 L40 4.2 L37.5 9 L35.5 4.6 L33 9.2 L31 5.8 L28.5 9.6Z"/><path fill="#57534e" d="M28 9.8 Q22 7.5 18.5 12.4 L22 11.6Z"/>' +
        '<g fill="#e7e5e4" opacity=".8"><circle cx="40" cy="12" r="1.1"/><circle cx="33" cy="13" r="1.1"/><circle cx="27" cy="14" r="1"/><circle cx="44" cy="15.5" r="1"/><circle cx="36" cy="17" r="1"/></g>' + eye(54, 12.8);
    },
    "sand bass": function () { return ART_BY["calico bass"](); },
    "white seabass": function () { return plainArt("#64748b", "#e2e8f0", "#475569"); },
    barracuda: function () { return plainArt("#475569", "#cbd5e1", "#334155", true); },
    wahoo: function () { return plainArt("#1d4ed8", "#cbd5e1", "#1e3a8a", true); },
    "striped marlin": function () { return plainArt("#1e3a8a", "#cbd5e1", "#3b82f6", true); },
    rockfish: function () { return plainArt("#b91c1c", "#fca5a5", "#7f1d1d"); },
    sheephead: function () { return plainArt("#1f2937", "#f472b6", "#111827"); },
    halibut: function () { return plainArt("#78716c", "#a8a29e", "#57534e"); },
    lingcod: function () { return plainArt("#4d7c0f", "#a3a3a3", "#365314"); },
    sardine: function () {
      return '<path fill="#64748b" d="M15 14 L6 8 Q8.5 15 6 22 L15 16Z"/>' +
        '<path fill="#0f766e" d="M60 15 C54 10.5 36 10 20 12.5 C17 13.3 15 14 13 15 C15 16 17 16.7 20 17.5 C36 20 54 19.5 60 15Z"/>' +
        '<path fill="#e2e8f0" d="M60 15 C54 18.5 36 19.5 20 17.5 C17 16.7 15 16 13 15 C28 16.2 46 16.6 60 15Z"/>' +
        '<g fill="#0f172a"><circle cx="46" cy="14" r=".6"/><circle cx="41" cy="14.2" r=".6"/><circle cx="36" cy="14.5" r=".6"/></g>' + eye(55.5, 14.2);
    },
    anchovy: function () {
      return '<path fill="#64748b" d="M15 14 L6 9 Q8.5 15 6 21 L15 16Z"/>' +
        '<path fill="#94a3b8" d="M60 15 C54 11.5 36 11 20 13 C17 13.6 15 14.2 13 15 C15 15.8 17 16.4 20 17 C36 19 54 18.5 60 15Z"/>' +
        '<path fill="#e2e8f0" d="M58 15.3 C46 15 30 15.2 16 15.4 L16 16 C30 16 46 16 58 15.9Z"/>' + eye(56, 14.3);
    },
    mackerel: function () {
      return plainArt("#0e7490", "#e2e8f0", "#155e75", true) +
        '<g stroke="#0f172a" stroke-width=".8"><path d="M50 12 L46 14.5"/><path d="M45 11.8 L41 14.5"/><path d="M40 11.8 L36 14.5"/><path d="M35 12 L31 14.5"/></g>';
    },
  };
  function fishIcon(name) {
    var k = String(name || "").toLowerCase(), f = ART_BY[k];
    if (!f) f = /tuna/.test(k) ? ART_BY.skipjack : /bass/.test(k) ? ART_BY["calico bass"] : /sardine|chovy/.test(k) ? ART_BY.sardine : /anchov/.test(k) ? ART_BY.anchovy : /mackerel/.test(k) ? ART_BY.mackerel : null;
    return svgFish(f ? f() : plainArt("#64748b", "#cbd5e1", "#475569"), name);
  }
  window.SDArt = { fishIcon: fishIcon };
})();
