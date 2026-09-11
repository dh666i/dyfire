import {defineConfig} from 'vite';
export default defineConfig({server:{proxy:{'/api':'http://127.0.0.1:8899','/accounts':'http://127.0.0.1:8899'}}});
