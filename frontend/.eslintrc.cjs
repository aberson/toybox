/* Minimal eslint config for the parent UI. Step 9 added this so the
   `npm run lint` gate has something to run; downstream phases may
   tighten rules.
*/
module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["@typescript-eslint", "react-hooks"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
  ],
  rules: {
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn",
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
    ],
    // Keep the surface practical for Phase A: do not block on style.
    "no-empty": ["error", { allowEmptyCatch: true }],
  },
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  ignorePatterns: ["node_modules", "dist", "playwright/**", "*.config.ts", "*.config.js", "*.cjs"],
};
