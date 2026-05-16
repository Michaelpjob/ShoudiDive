import { Component } from "react";

/**
 * Top-level error boundary around the map view.
 *
 * The 2026-05-07 white-screen incident shipped a ReferenceError that
 * threw inside React's first render of DesktopView. Without a boundary,
 * the whole tree unmounted and users saw a blank page until they hard-
 * refreshed past the cached HTML. With this boundary in place, the same
 * class of bug surfaces as a recoverable panel — the topbar still
 * renders, the user sees what broke, and a "Reload" button gets them
 * back to a working build.
 *
 * Scope is intentionally narrow: we wrap DesktopView (the part that
 * renders the map + timelines + overlays) but NOT TopBar / Settings.
 * That way a transient render bug in the map can't take out the topbar
 * status indicator — which the user needs to see "Demo data" / "Live"
 * to know whether their last reload helped.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // console.error so the runtime-smoke + live-cp-render Puppeteer
    // gates pick this up as a regression. The smoke tests fail on any
    // console.error inside the bundle's first paint window — without
    // this log, a render bug caught here would silently hide.
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught render error:", error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <div className="error-boundary-title">Map view crashed</div>
          <p className="error-boundary-body">
            Something broke while rendering the map. Reloading usually fixes
            it — the latest deploy may have just landed and the cached bundle
            is out of sync.
          </p>
          <pre className="error-boundary-detail mono">
            {String(this.state.error?.message || this.state.error || "Unknown error")}
          </pre>
          <button
            type="button"
            className="error-boundary-reload"
            onClick={() => window.location.reload()}
          >
            Reload page
          </button>
        </div>
      </div>
    );
  }
}
