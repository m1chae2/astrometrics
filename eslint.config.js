/**
 * ESLint settings for the front end (`ui/`) and the Electron shell
 * (`electron/`). Uses the "flat config" format, the only one ESLint 10 reads.
 *
 * The TypeScript parser below needs TypeScript's JavaScript API. TypeScript 7
 * (the fast native compiler that `npm run type-check` runs) does not have that
 * API yet. So package.json installs two copies, the way the TypeScript team
 * recommends: TypeScript 6 under the name `typescript` for tools like this
 * parser, and TypeScript 7 under the alias `@typescript/native`, which owns
 * the `tsc` command.
 */
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'

/** How many folders deep below `ui/common/` the folder rule checks. */
const COMMON_FOLDER_RULE_MAX_DEPTH = 8

/**
 * Builds the ESLint settings that stop `ui/common/` from importing any other
 * top-level `ui/` folder.
 *
 * ESLint's `no-restricted-imports` rule looks only at the import text, not at
 * the file it points to. So a file's depth below `ui/common/` decides how many
 * `../` steps lead out of `ui/common/`. This function makes one settings block
 * per depth. Each block bans an import that climbs exactly to `ui/` and then
 * enters a folder other than `common`. Imports that climb past `ui/` are left
 * alone, since this rule is only about the `ui/` folders.
 *
 * @returns {object[]} One flat-config block per folder depth.
 */
function commonFolderImportRules() {
  const blocks = []
  for (let depth = 0; depth <= COMMON_FOLDER_RULE_MAX_DEPTH; depth += 1) {
    const folders = '*/'.repeat(depth)
    const stepsUp = depth + 1
    blocks.push({
      files: [`ui/common/${folders}*.{js,jsx,ts,tsx}`],
      rules: {
        'no-restricted-imports': [
          'error',
          {
            patterns: [
              {
                regex: `^(\\.\\./){${stepsUp}}(?!common(/|$)|\\.\\.(/|$))`,
                message:
                  'ui/common is shared by the whole app and must not import another top-level ui/ folder. Move the shared code into ui/common instead.',
              },
            ],
          },
        ],
      },
    })
  }
  return blocks
}

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
  {
    // The app reaches the backend only through the public interface the
    // backend declares in backend/public_interface.py. These two files are
    // the only ones allowed to call `fetch` or open a WebSocket, and a backend
    // test checks every path they use against that declaration. Every other
    // app file goes through `callBackend`, `fetchImageFile` or the socket
    // helpers. Tests, the test set-up files and the ui/mcp tool server are
    // not part of the app, so they are left out.
    files: ['ui/**/*.{js,jsx,ts,tsx}'],
    ignores: [
      'ui/common/services/backendApi.ts',
      'ui/common/utils/socketClient.ts',
      'ui/**/*.test.{js,jsx,ts,tsx}',
      'ui/**/tests/**',
      'ui/setupTests.ts',
      'ui/globalSetup.ts',
      'ui/testBackendMarker.ts',
      'ui/mcp/**',
    ],
    rules: {
      'no-restricted-globals': [
        'error',
        {
          name: 'fetch',
          message: 'Call the backend through callBackend or fetchImageFile in ui/common/services/backendApi.ts.',
        },
        {
          name: 'WebSocket',
          message: 'Open backend sockets through ui/common/utils/socketClient.ts.',
        },
      ],
      'no-restricted-properties': [
        'error',
        { object: 'window', property: 'fetch', message: 'Use ui/common/services/backendApi.ts.' },
        { object: 'globalThis', property: 'fetch', message: 'Use ui/common/services/backendApi.ts.' },
        { object: 'window', property: 'WebSocket', message: 'Use ui/common/utils/socketClient.ts.' },
        { object: 'globalThis', property: 'WebSocket', message: 'Use ui/common/utils/socketClient.ts.' },
      ],
    },
  },
  {
    // The Electron shell runs in Node.js. Its files are ES modules (package.json
    // sets "type": "module"), except preload.js, which Electron loads as a
    // CommonJS script and which uses `require`.
    files: ['electron/**/*.js'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  {
    files: ['electron/preload.js'],
    languageOptions: {
      sourceType: 'commonjs',
      parserOptions: { sourceType: 'script' },
    },
  },
  {
    // Electron's main process reaches the backend only through
    // electron/backend_client.js, the same way the app uses backendApi.ts. A
    // backend test checks every path that file uses against
    // backend/public_interface.py.
    files: ['electron/**/*.js'],
    ignores: ['electron/backend_client.js'],
    rules: {
      'no-restricted-globals': [
        'error',
        {
          name: 'fetch',
          message: 'Call the backend through callRpc or checkReady in electron/backend_client.js.',
        },
        {
          name: 'WebSocket',
          message: 'Electron does not open backend sockets; add any backend call to electron/backend_client.js.',
        },
      ],
      'no-restricted-properties': [
        'error',
        { object: 'globalThis', property: 'fetch', message: 'Use electron/backend_client.js.' },
        { object: 'global', property: 'fetch', message: 'Use electron/backend_client.js.' },
        { object: 'globalThis', property: 'WebSocket', message: 'Use electron/backend_client.js.' },
        { object: 'global', property: 'WebSocket', message: 'Use electron/backend_client.js.' },
      ],
    },
  },
  // Shared code in ui/common must not depend on a feature folder such as
  // ui/astronomyManager. See commonFolderImportRules above.
  ...commonFolderImportRules(),
]
