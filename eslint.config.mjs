import tsParser from '@typescript-eslint/parser';

// Keep the repository's compact layout. Enforce correctness without a bulk
// style migration or rules that mistake type declarations for runtime names.
const rules = {
  'constructor-super': 'error',
  'for-direction': 'error',
  'getter-return': 'error',
  'no-async-promise-executor': 'error',
  'no-constant-condition': ['error', { checkLoops: false }],
  'no-dupe-args': 'error',
  'no-dupe-else-if': 'error',
  'no-duplicate-case': 'error',
  'no-func-assign': 'error',
  'no-import-assign': 'error',
  'no-self-assign': 'error',
  'no-unexpected-multiline': 'error',
  'no-unreachable': 'error',
  'no-unsafe-finally': 'error',
  'no-unsafe-negation': 'error',
  'valid-typeof': 'error',
};

export default [
  { ignores: ['node_modules/**', 'out/**', 'dist/**', '.validation/**', 'src/python/libs/**'] },
  { files: ['src/typescript/**/*.ts'], languageOptions: { parser: tsParser }, rules },
  { files: ['scripts/**/*.js', 'tests/**/*.js'],
    languageOptions: { ecmaVersion: 2022, sourceType: 'commonjs' }, rules },
];
