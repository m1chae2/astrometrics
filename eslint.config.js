import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'

export default [
  {
    // Build output, vendored third-party bundles, generated artifacts, and
    // scratch worktrees. Without these a bare `eslint .` reports tens of
    // thousands of problems from code nobody here wrote, which buries the
    // handful of real findings in hand-written sources.
    ignores: [
      'dist',
      'out',
      'out/**',
      'dist/**',
      'node_modules',
      '**/node_modules/**',
      '.venv',
      'electron-build',
      'public/js/**',          // vendored third-party browser libraries
      'documentation/_build/**', // generated Sphinx HTML and its bundled JS
      'coverage/**',           // generated coverage reports
      '.claude/**',            // agent scratch space, including git worktrees
      '.agent/**',
    ],
  },
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        ...globals.es2020,
        ...globals.node,
        ...globals.jest,
      },
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // ESLint's own baseline: catches plain JavaScript mistakes, like
      // referencing a variable that was never declared or unreachable code.
      ...js.configs.recommended.rules,
      // TypeScript ESLint's baseline: catches TypeScript-specific mistakes
      // the plain-JavaScript rules above don't understand, like an unsafe
      // non-null assertion or a misused type import.
      ...tsPlugin.configs.recommended.rules,
      // Catches React Hooks mistakes that cause real, hard-to-debug bugs --
      // most importantly a hook called conditionally or outside a
      // component, which breaks React's ability to track its state.
      ...reactHooks.configs.recommended.rules,

      // Off together: turning the TypeScript-aware version on surfaces
      // 100+ pre-existing "declared but never used" warnings across the
      // codebase that would each need individual review before this could
      // be enabled -- the plain-JavaScript version is redundant with it
      // and is turned off for the same reason.
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      // Off: this codebase legitimately uses `any` in ~170 places for
      // genuinely dynamic values (JSON from the backend, IPC/WebSocket
      // payloads, test mocks) where a precise type isn't practical --
      // the same reasoning behind ruff's own `any-type` exemption for
      // Python in pyproject.toml.
      '@typescript-eslint/no-explicit-any': 'off',
      // Off: flags calling setState directly inside useEffect, which can
      // trigger an extra render. ~38 existing effects do this on purpose
      // to sync local UI state off external state, and would need
      // individual review before this could be an error.
      'react-hooks/set-state-in-effect': 'off',
      // Warn, not off: missing effect dependencies are a real source of
      // bugs, but a handful of existing effects intentionally omit one --
      // each needs its specific intent understood before "fixing" it, so
      // this stays visible as a warning instead of being silenced or made
      // a hard error that would block CI on those known cases.
      'react-hooks/exhaustive-deps': 'warn',
      // Off: this codebase's React Context files intentionally export
      // both the context's Provider component and a matching useX() hook
      // from the same file (e.g. TargetContext.tsx) -- exactly the
      // pattern this rule warns about, but the standard way to keep a
      // context and its hook together rather than a mistake.
      'react-refresh/only-export-components': 'off',
      // Off: plain ESLint doesn't know about TypeScript's own global types
      // (e.g. `React`, `BufferSource`, `RequestInit`), so it misreports
      // them as undefined. TypeScript's compiler already checks for
      // genuinely undefined names -- correctly, since it understands
      // types -- in the separate "Typecheck and Unit Test UI" CI job.
      'no-undef': 'off',
    },
  },
]
