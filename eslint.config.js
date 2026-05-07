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
      "react-hooks": reactHooks,
    },
    rules: {
      // Inherit the recommended baseline.
      ...js.configs.recommended.rules,

      // ---- the rule that would have caught the white-screen bug ----
      "no-undef":                  "error",

      // ---- other no-mistake-no-cost rules --------------------------
      "no-unused-vars":            ["warn", {
        // Explicitly-prefixed-underscore identifiers are intentional —
        // common pattern for the throwaway side of a destructure.
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        // Catch identifiers that are introduced and never read at all.
        caughtErrors: "none",
      }],
      "no-unreachable":            "error",
      "no-self-compare":           "error",
      "no-self-assign":            "error",
      "no-duplicate-imports":      "error",
      "no-constant-condition":     ["error", { checkLoops: false }],
      "no-empty":                  ["error", { allowEmptyCatch: true }],
      "no-cond-assign":            ["error", "always"],

      // ---- React hooks gotchas. These cost nothing and catch real
      // bugs (stale closures, conditional hook calls).
      "react-hooks/rules-of-hooks":  "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
