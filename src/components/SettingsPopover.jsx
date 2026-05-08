// Settings popover — theme, opacity, units. Carved out of App.jsx
// (2026-05-09) as part of the Tier-1 architecture split. Pure
// presentational; all state lives in App.jsx and is mutated via
// the setPref callback.

export default function SettingsPopover({ prefs, setPref, onClose }) {
  return (
    <div className="settings-pop" role="dialog" aria-label="Settings">
      {/* Close button — was missing entirely; on mobile the popover
          covers most of the screen and there was no way to dismiss
          short of tapping the gear again, which most users wouldn't
          discover. */}
      {onClose && (
        <button
          type="button"
          className="sp-close"
          aria-label="Close settings"
          onClick={onClose}
        >
          ×
        </button>
      )}
      <div className="sp-section">
        <div className="sp-h">Theme</div>
        <div className="sp-row">
          <span>Appearance</span>
          <div className="sp-seg">
            <button
              className={prefs.theme === "light" ? "active" : ""}
              onClick={() => setPref("theme", "light")}
            >
              Light
            </button>
            <button
              className={prefs.theme === "dark" ? "active" : ""}
              onClick={() => setPref("theme", "dark")}
            >
              Dark
            </button>
          </div>
        </div>
      </div>
      <div className="sp-section">
        <div className="sp-h">Map</div>
        <div className="sp-row">
          <span>Overlay opacity</span>
          <span className="sp-val mono">{Math.round(prefs.opacity * 100)}%</span>
        </div>
        <input
          type="range"
          min={20}
          max={100}
          step={2}
          value={Math.round(prefs.opacity * 100)}
          onChange={(e) => setPref("opacity", Number(e.target.value) / 100)}
        />
      </div>
      <div className="sp-section">
        <div className="sp-h">Units</div>
        <div className="sp-row">
          <span>Temperature</span>
          <div className="sp-seg">
            <button
              className={prefs.units === "F" ? "active" : ""}
              onClick={() => setPref("units", "F")}
            >
              °F
            </button>
            <button
              className={prefs.units === "C" ? "active" : ""}
              onClick={() => setPref("units", "C")}
            >
              °C
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
