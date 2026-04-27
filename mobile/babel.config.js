// Babel configuration for the mobile app.
//
// react-native-reanimated v4 is required by @shopify/react-native-skia's
// Canvas renderer at runtime, even though Skia's package.json marks it
// as an "optional" peer dep. Skia uses a lazy proxy that only fails
// when you instantiate a <Canvas> — which means a missing reanimated
// passes bundle compilation but crashes the moment Canvas mounts.
//
// Reanimated v4 requires its worklets transform plugin in babel:
//   - In v3 the plugin lived at "react-native-reanimated/plugin"
//   - In v4 it moved to "react-native-worklets/plugin" (the worklets
//     runtime was extracted to its own package)
//
// IMPORTANT: the worklets plugin must be the LAST entry in `plugins`
// — it transforms code that other plugins may produce, so it has to
// run after them.
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
    plugins: ["react-native-worklets/plugin"],
  };
};
