import {defineConfig} from 'vite';
const backend=process.env.PORTFOLIO_API_ORIGIN||'http://127.0.0.1:8000';
export default defineConfig({server:{proxy:{'/api':backend,'/resume':backend}},build:{outDir:'dist'}});
