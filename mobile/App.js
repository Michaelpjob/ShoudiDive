// Day-1 entry. Just renders the map screen full-bleed; navigation +
// secondary screens (saved spots list, info pane) come once the
// primary surface feels right.
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import MapScreen from "./src/components/MapScreen.jsx";

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <MapScreen />
      <StatusBar style="auto" />
    </GestureHandlerRootView>
  );
}
