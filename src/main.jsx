import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles/app.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

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
