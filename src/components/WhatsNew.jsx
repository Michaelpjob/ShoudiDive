// "What's New" panel — user-facing release notes. Curated entries from
// src/data/changelog.js, grouped New / Improved / Fixed. Opened from the
// top-bar sparkle button (TopBar.jsx). Reuses the .mpa-popup* modal shell.

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { CHANGELOG } from "../data/changelog.js";

const TYPE_META = {
  new:      { label: "New",      cls: "wn-tag-new" },
  improved: { label: "Improved", cls: "wn-tag-improved" },
  fixed:    { label: "Fixed",    cls: "wn-tag-fixed" },
};

export default function WhatsNew({ onClose }) {
  useEffect(() => {
    const onKeyDown = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Portal to <body>: the trigger lives in the topbar, whose
  // position:relative would otherwise scope this absolute overlay to the
  // ~43px-tall bar. On body + position:fixed (see .wn-overlay) it covers
  // the viewport and centers correctly, like a top-level modal should.
  return createPortal(
    <div className="mpa-popup-overlay wn-overlay" onClick={onClose} role="presentation">
      <div
        className="mpa-popup wn-popup"
        role="dialog"
        aria-modal="true"
        aria-label="What's new"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="mpa-popup-close"
          onClick={onClose}
          aria-label="Close what's new"
        >
          ×
        </button>
        <div className="wn-head">
          <span className="wn-spark" aria-hidden="true">✦</span>
          <div>
            <div className="mpa-popup-name">What’s new</div>
            <div className="mpa-popup-fullname">Recent updates to ShouldIDive</div>
          </div>
        </div>

        <div className="wn-entries">
          {CHANGELOG.map((entry) => (
            <section key={entry.id} className="wn-entry">
              <div className="wn-entry-head">
                <h3 className="wn-entry-title">{entry.title}</h3>
                <span className="wn-entry-date">{entry.date}</span>
              </div>
              <ul className="wn-items">
                {entry.items.map((item, i) => {
                  const meta = TYPE_META[item.type] || TYPE_META.improved;
                  return (
                    <li key={i} className="wn-item">
                      <span className={"wn-tag " + meta.cls}>{meta.label}</span>
                      <span className="wn-item-text">{item.text}</span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>

        <button type="button" className="mpa-popup-done" onClick={onClose}>
          Got it
        </button>
      </div>
    </div>,
    document.body,
  );
}
