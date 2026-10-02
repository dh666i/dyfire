import {defineConfig} from 'vite';
export default defineConfig({
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
