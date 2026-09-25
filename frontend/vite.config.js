import {defineConfig} from 'vite';
export default defineConfig({
  optimizeDeps: { include: ['motion'] },
  resolve: { conditions: ['import', 'module', 'browser', 'default'] },
  server:{proxy:{'/api':'http://127.0.0.1:8899','/accounts':'http://127.0.0.1:8899'}}
});
