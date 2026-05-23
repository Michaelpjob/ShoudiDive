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
      "public/**",                // static assets (incl. third-party)
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
];
