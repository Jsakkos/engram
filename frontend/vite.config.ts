import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

// Read version from backend app/__init__.py:__version__ (single source of truth)
const versionPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend/app/__init__.py')
const versionContent = fs.readFileSync(versionPath, 'utf-8')
const versionMatch = versionContent.match(/__version__\s*=\s*"(.+?)"/)
const APP_VERSION = versionMatch ? versionMatch[1] : 'dev'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [
        react(),
        tailwindcss(),
    ],
    define: {
        __APP_VERSION__: JSON.stringify(APP_VERSION),
    },
    resolve: {
        alias: {
            '@': path.resolve(path.dirname(fileURLToPath(import.meta.url)), './src'),
        },
    },
    server: {
        port: parseInt(process.env.VITE_PORT || '5173', 10),
        proxy: {
            '/api': {
                target: `http://localhost:${process.env.VITE_BACKEND_PORT || '8000'}`,
                changeOrigin: true,
            },
            '/ws': {
                target: `ws://localhost:${process.env.VITE_BACKEND_PORT || '8000'}`,
                changeOrigin: true,
                ws: true,
                rewrite: (path) => path,
            },
        },
    },
})
