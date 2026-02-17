import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

// Read version from backend pyproject.toml (single source of truth)
const pyprojectPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend/pyproject.toml')
const pyprojectContent = fs.readFileSync(pyprojectPath, 'utf-8')
const versionMatch = pyprojectContent.match(/^version\s*=\s*"(.+?)"/m)
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
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://localhost:8000',
                changeOrigin: true,
                ws: true,
                rewrite: (path) => path,
            },
        },
    },
})
