import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// Use vitest/config so the `test` block is typed (vitest re-exports Vite config).
import { defineConfig } from "vitest/config";

const alias = {
  "@": fileURLToPath(new URL("./src", import.meta.url)),
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias },
  server: {
    proxy: {
      // Forward API calls to the FastAPI backend during development.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    // The FENCR golden-vector test only needs WebCrypto (Node has it natively),
    // but keep a DOM-ish environment so `Blob`/`btoa` behave as in the browser.
    environment: "node",
    globals: true,
  },
});
