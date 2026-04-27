// Babel configuration for the mobile app.
//
// Currently nothing beyond babel-preset-expo. We previously added the
// react-native-worklets plugin in support of react-native-reanimated,
// but reanimated v4 + Expo Go SDK 54 had bootstrap issues we couldn't
// reliably resolve without a custom dev client. Since we don't
// actually use animations yet, the simpler path is no reanimated at
// all. When we add a feature that needs it (e.g. an animated
// timeline scrubber), reinstall via `npx expo install
// react-native-reanimated` and re-add `react-native-worklets/plugin`
// here as the LAST entry in `plugins`.
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
  };
};
