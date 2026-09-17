import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The shared event contract lives one level up in /shared. It is a frozen
// contract owned jointly with the backend — we import its types, never edit it.
// Allow Vite's dev server to read files from the repo root so that import works.
export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [".."] },
  },
});
