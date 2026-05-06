// Shared drag-smoothing hook for SstTimeline / WindTimeline / SwellTimeline.
//
// The naive pattern these three components started with was:
//
//     onPointerMove → xToTarget(clientX) → setSel(target)
//
// That works, but it makes the UX feel clunky for two reasons:
//
//   1. The playhead position is a function of the *snapped* index, so
//      it jumps in coarse increments (one cell width per step).
//      Without a CSS transition that feels jerky; with a transition
//      it feels laggy because the finger and the indicator drift apart.
//
//   2. setSel() fires every time `xToTarget()` snaps to a new value.
//      For the wind/swell timelines (120 hourly slots) that's fine,
//      but the React tree it triggers — DataOverlay PNG decode + map
//      heatmap repaint — is heavy enough to skip frames during drag.
//
// This hook fixes both:
//
//   - Tracks the raw pixel offset under the finger (`dragX`) and
//     exposes a `playheadFracDuringDrag` so the component can paint
//     the indicator at the FINGER position during the drag.
//   - Only fires the supplied `onCommit(target)` callback when the
//     snapped target actually changes — so React state churn matches
//     cell crossings, not pointermove events.
//   - On pointerup we clear `dragX`, the playhead falls back to the
//     snapped position (via the existing `playheadFrac` math the
//     component already had), and the CSS transition on
//     `.tl-playhead` glides it home.
//
// The component still owns:
//   - the underlying selection state shape (slot string vs. day/hour)
//   - the math from clientX → target index/key
//   - the math from target → playhead fraction at rest
//
// We just give it smooth motion + commit throttling on top.

import { useCallback, useRef, useState } from "react";


/**
 * @param {object} args
 * @param {React.RefObject} args.ref       — the scrubber root element
 * @param {(clientX: number) => any}        args.xToTarget
 *        Convert a clientX into the discrete target value (slot index,
 *        global hour, etc). Same shape the component already uses.
 * @param {(target: any) => void}           args.onCommit
 *        Called whenever the snapped target changes during drag, plus
 *        once on pointerdown and once on pointerup. The component
 *        translates this into setSel({...}).
 * @param {any}                             args.currentTarget
 *        The component's current snapped target — passed in so we can
 *        skip onCommit when nothing changed.
 * @param {(currentTarget: any) => number}  args.targetToFrac
 *        Convert a target value to a 0..1 playhead fraction. Used so
 *        the hook can produce the playhead position without the
 *        component having to reach back in.
 *
 * @returns {{
 *   dragging: boolean,
 *   playheadFrac: number,                    — 0..1 to paint right now
 *   handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel,
 *               onKeyDown, tabIndex }
 * }}
 */
export function useTimelineDrag({
  ref,
  xToTarget,
  targetToFrac,
  onCommit,
  currentTarget,
  // Optional keyboard support — components pass in a stepper and the
  // total step count so ←/→ moves one step, Home/End jumps to ends.
  // When omitted, keyboard arrows are no-ops (back-compat).
  step = null,            // (currentTarget, deltaSteps) → newTarget
  totalSteps = null,
}) {
  const [dragging, setDragging] = useState(false);
  // Live raw pixel offset under the finger. null means "use snapped".
  const dragXRef = useRef(null);
  // Force re-renders when dragXRef changes (we don't want to setState
  // every pointermove because that re-triggers the component's whole
  // render tree — but we DO need React to repaint the playhead). A
  // tick counter is the cheapest opt-in re-render: only the
  // playheadFrac math reads it, the rest of the tree only re-renders
  // when currentTarget changes (which we throttle to snap crossings).
  const [, forceTick] = useState(0);

  const settled = targetToFrac(currentTarget);

  // While dragging, paint at finger position. While not dragging,
  // paint at the snapped position (which the CSS transition will
  // glide to from wherever the playhead was last drawn).
  const playheadFrac = (() => {
    const r = ref.current;
    if (!dragging || dragXRef.current == null || !r) return settled;
    const w = r.getBoundingClientRect().width;
    if (!w) return settled;
    return Math.max(0, Math.min(1, dragXRef.current / w));
  })();

  // ---- Pointer handlers ------------------------------------------------

  const updatePosition = useCallback((clientX) => {
    const r = ref.current;
    if (!r) return;
    const rect = r.getBoundingClientRect();
    const px = Math.max(0, Math.min(rect.width, clientX - rect.left));
    dragXRef.current = px;
    forceTick((t) => t + 1);

    const target = xToTarget(clientX);
    // Only call onCommit when the snapped target actually changed —
    // React then only re-renders + re-paints the heatmap on cell
    // crossings, not on every pixel. Cuts mid-drag stutter dramatically.
    if (target !== currentTarget) {
      onCommit(target);
    }
  }, [ref, xToTarget, onCommit, currentTarget]);

  const onPointerDown = useCallback((e) => {
    setDragging(true);
    updatePosition(e.clientX);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }, [updatePosition]);

  const onPointerMove = useCallback((e) => {
    if (!dragging) return;
    updatePosition(e.clientX);
  }, [dragging, updatePosition]);

  const onPointerUp = useCallback((e) => {
    setDragging(false);
    // Clear the raw pixel position. The component falls back to the
    // snapped frac via targetToFrac(currentTarget), and the CSS
    // transition on .tl-playhead's `left` glides the playhead home.
    dragXRef.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }, []);

  // ---- Keyboard support -----------------------------------------------
  // role="slider" gets keyboard treatment for free if we wire arrow keys.
  // Implementation note: focus comes from tabIndex below; we also use
  // .focus-visible so the focus ring only paints on keyboard focus, not
  // mouse click — same convention as the rest of the app.
  const onKeyDown = useCallback((e) => {
    if (!step) return;
    let delta = 0;
    let jumpTo = null;
    switch (e.key) {
      case "ArrowLeft":  delta = -1; break;
      case "ArrowRight": delta = +1; break;
      case "ArrowDown":  delta = -1; break;  // screen reader convention
      case "ArrowUp":    delta = +1; break;
      case "PageDown":   delta = -7; break;  // jump a week
      case "PageUp":     delta = +7; break;
      case "Home":       jumpTo = 0; break;
      case "End":        jumpTo = (totalSteps ?? 0); break;
      default: return;   // not handled — let the event bubble
    }
    e.preventDefault();
    if (jumpTo !== null) {
      const t = step(currentTarget, jumpTo - currentTarget);
      if (t !== currentTarget) onCommit(t);
    } else {
      const t = step(currentTarget, delta);
      if (t !== currentTarget) onCommit(t);
    }
  }, [step, totalSteps, currentTarget, onCommit]);

  return {
    dragging,
    playheadFrac,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
      tabIndex: 0,
    },
  };
}
