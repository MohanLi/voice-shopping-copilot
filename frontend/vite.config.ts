import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on :5173 and talks to the API on :8000 (`voiceshop serve`).
// The production build goes to dist/, which the API serves itself.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
});
