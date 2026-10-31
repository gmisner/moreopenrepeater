import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built assets land in web/dist so the Pi checkout can serve them with no Node.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../web/dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/login": "http://127.0.0.1:8000",
      "/listen": "http://127.0.0.1:8000",
    },
  },
});
