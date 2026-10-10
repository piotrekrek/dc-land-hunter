/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API (uvicorn :8000) and Martin (:3000) run separately; in Docker, Caddy routes them.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    rollupOptions: {
      // MapLibre is large and changes rarely: keep it in its own long-cached chunk.
      output: { manualChunks: { maplibre: ["maplibre-gl"], vendor: ["react", "react-dom", "react-router", "@tanstack/react-query"] } },
    },
    chunkSizeWarningLimit: 1100,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/tiles": { target: "http://localhost:3000", rewrite: (path) => path.replace(/^\/tiles/, "") },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
    globals: true,
  },
});
