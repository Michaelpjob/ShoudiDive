// Day-1 entry. Just renders the map screen full-bleed; navigation +
// secondary screens (saved spots list, info pane) come once the
// primary surface feels right.
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";

// No extension on the import path so Metro can resolve to
// MapScreen.web.jsx on web and MapScreen.jsx on iOS / Android. With
// the .jsx suffix Metro picks that file verbatim and the web build
// fails because react-native-maps is iOS / Android only.
import MapScreen from "./src/components/MapScreen";

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <MapScreen />
      <StatusBar style="auto" />
    </GestureHandlerRootView>
  );
}
