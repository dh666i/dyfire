import {defineConfig} from 'vite';
import {readFile} from 'node:fs/promises';
export default defineConfig({
  plugins: [{
    name: 'local-brand-assets',
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url?.split('?')[0] !== '/static/spark-flame.png') return next();
        try {
          const image = await readFile(new URL('../spark_console/static/spark-flame.png', import.meta.url));
          response.setHeader('Content-Type', 'image/png'); response.end(image);
        } catch { next(); }
      });
    },
  }, {
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
    // shell owns auth/bootstrap; the administrator route and charts load
    // through a separate lazy module. Vendor chunks reduce invalidation when
    // either route changes, without duplicating application/session state.
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-motion': ['motion'],
          'vendor-ui': ['@base-ui/react/select', '@base-ui/react/drawer', 'sonner', '@number-flow/react'],
        },
      },
    },
  },
  server:{proxy:{'/api':'http://127.0.0.1:8899','^/accounts/(scan|recovery|[^/]+/(health-check|sync|conversations))':'http://127.0.0.1:8899'}}
});
