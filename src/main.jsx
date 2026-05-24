import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/app.css";
import { initAnalytics } from "./lib/analytics.js";
import { PrefsProvider } from "./contexts/PrefsContext.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <PrefsProvider>
      <App />
    </PrefsProvider>
  </React.StrictMode>
);

// Privacy-respecting in-app analytics. Honors Do-Not-Track + a
// localStorage opt-out flag. No cookies, no third-party trackers,
// no PII — events post to our own /api/analytics/event Pages
// Function. See src/lib/analytics.js for the contract +
// functions/api/analytics/event.js for the receiver.
//
// Init AFTER the React tree mounts so the pageview event isn't
// counted before the app actually renders something visible.
initAnalytics();

// Register the service worker after the page has settled. Production only —
// during dev the SW would intercept hot-reload assets and break Vite.
// The SW lives at public/sw.js and ships as `/sw.js` after Vite copies it.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("Service worker registration failed", err);
    });
  });
  // When a new SW activates and takes control (e.g. we shipped a fix and
  // bumped the cache version), reload so the live tab picks up the
  // fresh shell instead of the stale cached one. Without this, users
  // keep seeing the old cached bundle until they manually reload.
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
}
