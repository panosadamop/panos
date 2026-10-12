/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The hub serves the built files (strict CSP: no inline script or style).
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { target: "es2022", outDir: "dist", sourcemap: false, assetsInlineLimit: 0 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"] },
});
