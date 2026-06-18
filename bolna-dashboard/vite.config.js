import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5174, host: true },
  build: {
    // recharts is ~510kB as one isolated vendor chunk; app code is ~78kB.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // Split heavy vendors so no single chunk trips the size advisory.
        manualChunks: {
          react: ['react', 'react-dom'],
          charts: ['recharts'],
          icons: ['lucide-react'],
        },
      },
    },
  },
})
