import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the backend. That is why the demo path needs no
// CORS relaxation at all: to the browser, the API is same-origin (PRD 10.4).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
