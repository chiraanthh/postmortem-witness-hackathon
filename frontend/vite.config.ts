import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The shared event contract lives one level up in /shared. Allow Vite's
// dev server to read files from the repo root so that import works.
// Proxy REST + WS to the FastAPI backend so the browser stays same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [".."] },
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/health": "http://127.0.0.1:8000",
      "/incident": "http://127.0.0.1:8000",
      "/export": "http://127.0.0.1:8000",
    },
  },
});
