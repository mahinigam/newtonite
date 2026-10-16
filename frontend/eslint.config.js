// Lint configuration.
//
// The type checker already catches most of what a linter would, so this is deliberately
// narrow: the React hook rules, which catch stale-closure bugs that `tsc` cannot see, plus
// the core correctness rules. Type-aware linting is not enabled — it would need every file
// to be in a program, for a marginal gain over `strict` TypeScript on a codebase this size.
//
// `eslint-plugin-react-refresh` and the JSX accessibility plugin are not included: there is
// one entry point and the components are not hot-reload boundaries worth policing, and the
// accessibility review was done by hand rather than by a rule set.

import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },

  js.configs.recommended,
  ...tseslint.configs.recommended,

  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.flat['recommended-latest'].rules,

      // An unused variable is a mistake unless it is deliberately discarded with `_`.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],

      // `any` defeats the point of the type checker; the client parses `unknown`.
      '@typescript-eslint/no-explicit-any': 'error',

      // Prefer `import type` so a type-only import cannot create a runtime cycle.
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
    },
  },

  {
    files: ['vite.config.ts', 'eslint.config.js'],
    languageOptions: { globals: { ...globals.node } },
  },

  {
    // Tests reach into the DOM and stub globals; the rules that make sense in application
    // code (no non-null assertions on query results, say) would only get in the way here.
    files: ['src/**/*.test.{ts,tsx}', 'src/test/**/*.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-non-null-assertion': 'off',
    },
  },
)
