import {defineConfig} from 'vite';
import path from 'path';
export default defineConfig({
  resolve: {
    alias: {
      'motion/react': path.resolve('./node_modules/motion/dist/es/react.mjs'),
    }
  },
  server:{proxy:{'/api':'http://127.0.0.1:8899','/accounts':'http://127.0.0.1:8899'}}
});
