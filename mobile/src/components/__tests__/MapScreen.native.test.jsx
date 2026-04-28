import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";
import { Image } from "react-native";

import MapScreen from "../MapScreen.jsx";
import { BBOX_RING } from "../../lib/mapData.js";

const mockFitToCoordinates = jest.fn();
const mockLoadManifest = jest.fn();
const mockSubscribe = jest.fn();
const mockIsReady = jest.fn();
const mockGetLayerPngUrl = jest.fn();

jest.mock("react-native-maps", () => {
  const React = require("react");
  const { View } = require("react-native");

  const MapView = React.forwardRef(({ children, ...props }, ref) => {
    React.useImperativeHandle(ref, () => ({
      fitToCoordinates: mockFitToCoordinates,
    }));
    return (
      <View testID="map-view" {...props}>
        {children}
      </View>
    );
  });

  function Overlay(props) {
    return (
      <View
        testID="map-overlay"
        accessibilityLabel={`overlay-uri:${props.image?.uri || "missing"}`}
        {...props}
      />
    );
  }

  function Marker(props) {
    return <View testID="map-marker" {...props} />;
  }

  return {
    __esModule: true,
    default: MapView,
    Marker,
    Overlay,
    PROVIDER_DEFAULT: undefined,
  };
});

jest.mock("../../lib/dataSource.js", () => ({
  loadManifest: (...args) => mockLoadManifest(...args),
  subscribe: (...args) => mockSubscribe(...args),
  isReady: (...args) => mockIsReady(...args),
  getLayerPngUrl: (...args) => mockGetLayerPngUrl(...args),
}));

function layerUrl(layer, composite = null) {
  if (layer === "viz") return "https://example.test/viz-now.png";
  return `https://example.test/${layer}-${composite ?? 2}d.png`;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flushAsync() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function renderAndPrime() {
  const utils = render(<MapScreen />);
  const map = utils.getByTestId("map-view");

  act(() => {
    map.props.onLayout({ nativeEvent: { layout: { width: 390, height: 844 } } });
    map.props.onMapReady();
  });

  act(() => {
    jest.runAllTimers();
  });

  await flushAsync();
  return utils;
}

describe("MapScreen native behavior", () => {
  let prefetchSpy;

  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();

    mockLoadManifest.mockResolvedValue(null);
    mockSubscribe.mockReturnValue(jest.fn());
    mockIsReady.mockReturnValue(true);
    mockGetLayerPngUrl.mockImplementation((layer, composite) => layerUrl(layer, composite));

    prefetchSpy = jest.spyOn(Image, "prefetch").mockResolvedValue(true);
  });

  afterEach(() => {
    prefetchSpy.mockRestore();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it("renders the default overlay, markers, and fits the known bbox once", async () => {
    const view = await renderAndPrime();

    expect(mockLoadManifest).toHaveBeenCalledTimes(1);
    expect(mockSubscribe).toHaveBeenCalledTimes(1);
    expect(prefetchSpy).toHaveBeenCalledWith("https://example.test/sst-2d.png");
    expect(view.getByTestId("map-overlay").props.accessibilityLabel).toBe(
      "overlay-uri:https://example.test/sst-2d.png"
    );
    expect(view.getAllByTestId("map-marker")).toHaveLength(8);
    expect(mockFitToCoordinates).toHaveBeenCalledTimes(1);
    expect(mockFitToCoordinates).toHaveBeenCalledWith(BBOX_RING, {
      edgePadding: { top: 16, right: 16, bottom: 16, left: 16 },
      animated: false,
    });
    expect(view.getByText(/Sea Temp\s*\.\s*2-day/)).toBeTruthy();
  });

  it("switches layers and updates the overlay URL without losing composite controls", async () => {
    const view = await renderAndPrime();

    fireEvent.press(view.getByText("Chl"));
    await flushAsync();

    expect(prefetchSpy).toHaveBeenLastCalledWith("https://example.test/chl-2d.png");
    expect(view.getByTestId("map-overlay").props.accessibilityLabel).toBe(
      "overlay-uri:https://example.test/chl-2d.png"
    );
    expect(view.getByText(/Chlorophyll/)).toBeTruthy();
    expect(view.getByText("1-day")).toBeTruthy();
    expect(view.getByText("3-day")).toBeTruthy();
  });

  it("switches to visibility and hides the composite picker", async () => {
    const view = await renderAndPrime();

    fireEvent.press(view.getByText("Vis"));
    await flushAsync();

    expect(prefetchSpy).toHaveBeenLastCalledWith("https://example.test/viz-now.png");
    expect(view.getByTestId("map-overlay").props.accessibilityLabel).toBe(
      "overlay-uri:https://example.test/viz-now.png"
    );
    expect(view.getByText(/Visibility/)).toBeTruthy();
    expect(view.queryByText("1-day")).toBeNull();
    expect(view.queryByText("2-day")).toBeNull();
    expect(view.queryByText("3-day")).toBeNull();
  });

  it("surfaces prefetch errors and does not mount an overlay", async () => {
    prefetchSpy.mockRejectedValueOnce(new Error("boom"));
    const view = await renderAndPrime();

    expect(view.queryByTestId("map-overlay")).toBeNull();
    expect(view.getByText(/overlay:error.*boom/)).toBeTruthy();
  });

  it("ignores stale prefetch completions when the user switches layers quickly", async () => {
    const pending = new Map();
    prefetchSpy.mockImplementation((url) => {
      const task = deferred();
      pending.set(url, task);
      return task.promise;
    });

    const view = await renderAndPrime();
    expect(view.queryByTestId("map-overlay")).toBeNull();

    fireEvent.press(view.getByText("Chl"));
    await flushAsync();

    act(() => {
      pending.get("https://example.test/sst-2d.png").resolve(true);
    });
    await flushAsync();

    expect(view.queryByTestId("map-overlay")).toBeNull();

    act(() => {
      pending.get("https://example.test/chl-2d.png").resolve(true);
    });
    await flushAsync();

    expect(view.getByTestId("map-overlay").props.accessibilityLabel).toBe(
      "overlay-uri:https://example.test/chl-2d.png"
    );
  });
});
