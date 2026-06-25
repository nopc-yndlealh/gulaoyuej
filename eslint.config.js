import htmlPlugin from 'eslint-plugin-html';
import globals from 'globals';

export default [
  // 浏览器 / 全局环境
  {
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.es2021,
      },
    },
  },
  // 基础推荐规则
  {
    rules: {
      // 语义错误 — 全部开启
      'no-undef': 'error',
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'no-redeclare': 'error',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
      'no-dupe-keys': 'error',
      'no-dupe-args': 'error',
      'no-empty': ['error', { allowEmptyCatch: false }],
      'no-extra-semi': 'error',
      'no-irregular-whitespace': 'error',
      'no-mixed-spaces-and-tabs': 'error',
      'no-sparse-arrays': 'error',
      'no-unexpected-multiline': 'error',
      'no-unreachable': 'error',
      'no-unsafe-finally': 'error',
      'use-isnan': 'error',
      'valid-typeof': 'error',

      // 最佳实践
      eqeqeq: ['error', 'always'],
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-extend-native': 'error',
      'no-new-func': 'error',
      'no-script-url': 'error',
      'no-self-compare': 'error',
      'no-sequences': 'error',
      'no-throw-literal': 'error',
      'no-useless-call': 'error',
      'no-useless-concat': 'error',
      'no-useless-return': 'error',
      'prefer-const': 'warn',
      'no-var': 'warn',
      curly: ['error', 'multi-line'],

      // ES6+ 偏好
      'arrow-spacing': 'error',
      'no-duplicate-imports': 'error',
      'no-useless-computed-key': 'error',
      'no-useless-rename': 'error',
      'object-shorthand': 'warn',
      'prefer-arrow-callback': 'warn',
      'prefer-template': 'warn',
    },
  },
  // HTML 内联脚本
  {
    files: ['**/*.html'],
    plugins: {
      html: htmlPlugin,
    },
    settings: {
      'html/indent': '+2',
    },
  },
  // 忽略构建产物和数据
  {
    ignores: ['data/**', 'images/**', 'node_modules/**', '*.py'],
  },
];
