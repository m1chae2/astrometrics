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
      ...js.configs.recommended.rules,
      ...tsPlugin.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': 'off',
      'no-undef': 'off',
    },
  },
]
