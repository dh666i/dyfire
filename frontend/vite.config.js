import {defineConfig} from 'vite';
export default defineConfig({
  plugins: [{
    name: 'fontsource-woff2-only',
    enforce: 'pre',
    transform(code, id) {
      // Keep the package's complete unicode-range coverage and license while
      // avoiding a second, obsolete WOFF copy of every Chinese subset.
      if (id.replaceAll('\\', '/').includes('/@fontsource/') && id.endsWith('.css')) {
        return {code: code.replace(/,\s*url\([^)]*\.woff\)\s*format\('woff'\)/g, ''), map: null};
      }
    },
  }],
  optimizeDeps: { include: ['motion'] },
  resolve: { conditions: ['import', 'module', 'browser', 'default'] },
  build: {
    // Keep framework code in long-lived cacheable chunks. The application
    // entry currently owns the authenticated route switch, so splitting
    // pages themselves would duplicate auth/bootstrap state or change route
    // timing. Vendor chunks still reduce invalidation when app code changes.
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-motion': ['motion'],
        },
      },
    },
  },
  server:{proxy:{'/api':'http://127.0.0.1:8899','/accounts':'http://127.0.0.1:8899'}}
});
