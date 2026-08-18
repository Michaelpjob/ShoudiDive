// ESLint flat config — primarily here to catch the exact class of bug
// that crashed production (commit 7d641696): a `ReferenceError: X is
// not defined` on first React render after a botched rebase removed
// a state declaration but left a downstream reference.
//
// `no-undef` would have flagged the dangling `sstViewMode` reference
// at lint time, before Vite ever bundled it. The rest of the rules
// here are minimal — we deliberately keep the rule set tight so the
// lint job stays fast (<5s) and doesn't generate noise that gets
// ignored or whitelisted away.
//
// To run locally:  npm run lint
// CI:              dev-checks.yml job `web-lint`
//
// Adding rules: keep the bias toward "would catch a real production
// bug." Style preferences belong in a formatter (prettier), not here.

import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";


export default [
  // ---------------------------------------------------------------------
  // Files to ignore. ESLint walks the whole repo by default; these are
  // entire trees that aren't ours to lint or aren't user-facing JS.
  // ---------------------------------------------------------------------
  {
    ignores: [
      "dist/**",                  // Vite build output
      "node_modules/**",
      "mobile/**",                // mobile/ has its own toolchain
      "flutter_app/**",           // Flutter app graveyard
      "pipeline/**",              // Python — out of scope
      // public/ is mostly static assets, but public/paddies/ ships OUR
      // code as plain <script> bundles rather than through Vite. Ignoring
      // the whole tree left the entire Kelp Paddy Finder unlinted, which
      // is not a hypothetical gap: the corridor geometry lives there and
      // shipped a drawn width that disagreed with its own printed number.
      //
      // So name the files that aren't ours instead of the directory. A
      // "public/**" entry cannot be walked back with a "!" negation —
      // ESLint prunes an ignored directory wholesale rather than
      // descending into it — so the exclusion has to be this specific.
      "public/paddies/leaflet.js", // vendored third-party
      "public/sw.js",              // service worker, its own global scope
      "**/*.min.js",
      "scripts/**",               // bash + node setup scripts
    ],
  },

  // ---------------------------------------------------------------------
  // Source files: src/ + tests/ + repo-root JS entry points.
  // Browser globals (window, document, etc.) plus Node globals for the
  // tests/*.test.js files which run under `node --test`.
  // ---------------------------------------------------------------------
  {
    files: ["src/**/*.{js,jsx,mjs}", "tests/**/*.{js,mjs}", "*.{js,mjs}"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    settings: {
      react: {
        // We don't import React types — Vite + the new JSX transform
        // handle that automatically. Declaring `version: "detect"`
        // would force the plugin to read node_modules/react/package.json
        // on every lint run; pinning here keeps the lint step under 5 s.
        version: "18.3",
      },
    },
    rules: {
      // Inherit the recommended baseline.
      ...js.configs.recommended.rules,

      // ---- the rule that would have caught the white-screen bug ----
      "no-undef":                  "error",

      // ---- other no-mistake-no-cost rules --------------------------
      // 2026-05-22 (Stage 1): re-enabled now that eslint-plugin-react is
      // present. `react/jsx-uses-vars` (auto-loaded by the plugin) tells
      // base ESLint that <Foo/> counts as a "use" of Foo, so this rule
      // no longer false-positives on every JSX-imported component.
      // Set to `warn` (not error) so a real dead-import surfaces in
      // the lint job without blocking the merge — the goal is to drive
      // the codebase to zero unused identifiers gradually, not to make
      // every refactor PR wait on a cleanup step.
      "no-unused-vars":            ["warn", {
        args: "none",                   // function args often documented but unused
        ignoreRestSiblings: true,       // `const {x, ...rest} = obj;` shouldn't warn on x
        varsIgnorePattern: "^_",        // `_unused` is the standard "I know" prefix
        argsIgnorePattern: "^_",
      }],
      "no-unreachable":            "error",
      "no-self-compare":           "error",
      "no-self-assign":            "error",
      "no-duplicate-imports":      "error",
      "no-constant-condition":     ["error", { checkLoops: false }],
      "no-empty":                  ["error", { allowEmptyCatch: true }],
      // "except-parens" allows the canonical `while ((m = regex.exec(s)))`
      // idiom when the assignment is explicitly parenthesized — flags
      // the genuinely-mistaken cases (`if (x = y)` without parens) but
      // doesn't fight common JS patterns.
      "no-cond-assign":            ["error", "except-parens"],

      // ---- React hooks gotchas. These cost nothing and catch real
      // bugs (stale closures, conditional hook calls).
      "react-hooks/rules-of-hooks":  "error",
      "react-hooks/exhaustive-deps": "warn",

      // ---- React plugin: keep ONLY the rules that catch real bugs.
      // The plugin's "recommended" config drags in dozens of style
      // rules (prop-types, jsx-key on every map, etc.) that would
      // generate noise without catching production failures. Pin the
      // ones that actually matter:
      "react/jsx-uses-vars":         "error",  // makes no-unused-vars JSX-aware
      "react/jsx-uses-react":        "off",    // new JSX transform — React not in scope
      // 2026-05-23: base ESLint `no-undef` does NOT catch undeclared
      // JSX tags — `<MoonWidget/>` slips through because the JSX
      // transform turns it into `_jsx(MoonWidget, ...)` and the rule
      // sees the variable read but flags it elsewhere. The dedicated
      // react/jsx-no-undef rule is the one that catches it. Without
      // this, the Stage 4 refactor shipped a no-undef bug for
      // <MoonWidget/> that web-smoke caught at runtime ("MoonWidget
      // is not defined") AFTER lint reported green. Same failure
      // class as the 2026-05-07 white-screen incident this whole
      // config exists to prevent — close the door.
      "react/jsx-no-undef":          "error",
    },
  },

  // ---------------------------------------------------------------------
  // public/paddies/ — the Kelp Paddy Finder bundle. Plain <script> files,
  // NOT modules and NOT built by Vite, so they need their own parser
  // settings: script sourceType (top-level `var PT = ...` is a global,
  // not an export) and no JSX/React.
  //
  // They share globals across files the way script tags do: track.js
  // defines PT, trackui.js and app.js consume it, and leaflet provides L.
  // Declaring those here is what lets no-undef stay on and still be
  // meaningful, rather than drowning in false positives.
  // ---------------------------------------------------------------------
  {
    files: ["public/paddies/{app,track,trackui}.js"],
    languageOptions: {
      ecmaVersion: 2020,          // the bundle targets older mobile Safari
      sourceType: "script",
      globals: {
        ...globals.browser,
        L: "readonly",            // leaflet, loaded by its own script tag
        PT: "writable",           // defined in track.js, read by trackui.js
        PTUI: "writable",         // defined in trackui.js, mounted by app.js
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-undef":                  "error",
      // PT and PTUI look unused to a single-file linter: each is defined
      // in one script and consumed by another via the shared global scope.
      "no-unused-vars":            ["warn", {
        args: "none",
        varsIgnorePattern: "^_|^(PT|PTUI)$",
      }],
      "no-unreachable":            "error",
      "no-self-compare":           "error",
      "no-self-assign":            "error",
      "no-constant-condition":     ["error", { checkLoops: false }],
      "no-empty":                  ["error", { allowEmptyCatch: true }],
      "no-cond-assign":            ["error", "except-parens"],
      // `var` in these files is deliberate (script scope, old targets), so
      // redeclaration is the realistic footgun rather than a style nit. It
      // caught a real one on first run: draw() declared `var i` for a loop
      // and again for the step index. builtinGlobals is off because PT and
      // PTUI are declared above as shared globals AND defined with `var` in
      // their own file, which is the correct pattern for script tags.
      "no-redeclare":              ["error", { builtinGlobals: false }],
      "no-dupe-keys":              "error",
      "no-fallthrough":            "error",
    },
  },
];
