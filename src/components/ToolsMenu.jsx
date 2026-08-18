// ToolsMenu — the compact launcher for the standalone beta tools.
//
// Why a dropdown and not a row of links: on phones the topbar only has
// room for the brand mark plus a couple of icon buttons, so the old
// "Paddy Finder" pill was hidden on mobile and its only phone home was
// a row buried at the bottom of the sheet's More section. One icon that
// breaks out into a menu costs the same 28px on every screen size and
// puts the tools two taps from anywhere.
//
// Entries deep-link where it makes sense: "Track a paddy" opens the
// paddies page with ?track=1, which the tool reads to open the tracker
// panel directly (public/paddies/trackui.js).
//
// CA-gated by the caller — the tools are Southern-California-Bight-only.

import { useEffect, useRef, useState } from "react";
import { track } from "../lib/analytics.js";

const TOOLS = [
  {
    href: "/paddies/",
    icon: "🪸",
    name: "Kelp Paddy Finder",
    sub: "live paddy likelihood map",
  },
  {
    href: "/paddies/?track=1",
    icon: "◎",
    name: "Track a paddy",
    sub: "7-day drift forecast from your mark",
  },
];

export default function ToolsMenu() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  // Close on outside click / Escape — same behaviour users expect from
  // the settings popover.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="tools-menu" ref={rootRef}>
      <button
        className="icon-btn tools-btn"
        aria-label="Beta tools"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Beta tools — Kelp Paddy Finder"
        onClick={() => setOpen((o) => !o)}
      >
        {/* Inline SVG, not an emoji: the topbar's other icon buttons are
            SVGs because emoji coverage varies by device (headless and
            older Androids render tofu). 2x2 grid = "more tools". */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      </button>
      {open && (
        <div className="tools-pop" role="menu" aria-label="Beta tools">
          <div className="tools-pop-h">
            Beta tools <span className="tool-link-beta">BETA</span>
          </div>
          {TOOLS.map((t) => (
            <a
              key={t.href}
              href={t.href}
              role="menuitem"
              className="tools-row"
              onClick={() => track("tool_open", { tool: t.href })}
            >
              <span className="tools-row-icon" aria-hidden="true">{t.icon}</span>
              <span className="tools-row-text">
                <span className="tools-row-name">{t.name}</span>
                <span className="tools-row-sub">{t.sub}</span>
              </span>
            </a>
          ))}
        </div>
      )}
    </span>
  );
}
