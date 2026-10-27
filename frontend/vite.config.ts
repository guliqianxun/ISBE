/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// `npm run dev` serves the app on :5173 and forwards data requests to a local
// `isbe serve` (default 127.0.0.1:8080; set ISBE_DEV_TARGET to change it). The
// target's own Host is sent, so the console's Host guard accepts the requests.
const target = process.env.ISBE_DEV_TARGET ?? 'http://127.0.0.1:8080';
const proxied = { target, changeOrigin: true };

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // The console serves the built files under /app/ (see isbe/web/routes.py).
  base: command === 'build' ? '/app/' : '/',
  build: {
    outDir: '../isbe/_assets/app',
    emptyOutDir: true,
    // No inline scripts or data: modules: the console's CSP is script-src 'self'.
    assetsInlineLimit: 0,
  },
  server: {
    // The shared stylesheet lives in ../web until the classic console is retired.
    fs: { allow: ['..'] },
    proxy: { '/api': proxied, '/figures': proxied, '/help/service': proxied },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // `npm run coverage`: a text summary for the log, Cobertura XML for GitLab's
    // merge request diff, and an HTML report kept as a CI artifact.
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**'],
      reporter: ['text-summary', 'text', 'cobertura', 'html'],
      reportsDirectory: './coverage',
    },
  },
}));
