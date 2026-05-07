export const MAP_GESTURE_CHILD_SELECTOR = [
  ".wind-timeline",
  ".swell-timeline",
  ".sst-timeline",
  ".current-timeline",
  ".mobile-shell",
  ".panel",
  ".moon-widget",
  ".zoom-ctl",
].join(", ");

export const SLIDER_GUARD_PX = 140;

export function isMapGestureChildTarget(target) {
  return Boolean(
    target &&
    typeof target.closest === "function" &&
    target.closest(MAP_GESTURE_CHILD_SELECTOR)
  );
}

export function layerHasTopTimeline(layer, hasSstHistory = false) {
  return (
    layer === "wind" ||
    layer === "swell" ||
    layer === "current" ||
    (layer === "sst" && hasSstHistory)
  );
}

export function shouldPinMapTap({
  tap,
  layer,
  hasSstHistory = false,
  sliderGuardPx = SLIDER_GUARD_PX,
  now = Date.now(),
  target = null,
} = {}) {
  if (!tap || tap.moved) return false;
  if (now - tap.startTime >= 350) return false;
  if (isMapGestureChildTarget(target)) return false;
  return !(layerHasTopTimeline(layer, hasSstHistory) && tap.startY < sliderGuardPx);
}
