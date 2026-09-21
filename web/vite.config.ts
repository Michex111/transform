import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// Use vitest/config so the `test` block is typed (vitest re-exports Vite config).
import { defineConfig } from "vitest/config";
import { spaRouteStubs } from "./vite-plugins/spa-route-stubs";

const alias = {
  "@": fileURLToPath(new URL("./src", import.meta.url)),
};

// https://vite.dev/config/
export default defineConfig({
  // `spaRouteStubs` makes client-side routes resolvable on the static host so
  // deep links and the post-Checkout redirect don't 404. See the plugin file.
  plugins: [react(), tailwindcss(), spaRouteStubs()],
  resolve: { alias },
  server: {
    proxy: {
      // Forward API calls to the FastAPI backend during development.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // The API also serves its OpenAPI docs and health probes at its *root*
      // (`/docs`, `/health`), which the Support page links to. Forward those too,
      // otherwise they would be served by the SPA router (an empty page).
      "/docs": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    // The FENCR golden-vector test only needs WebCrypto (Node has it natively),
    // but keep a DOM-ish environment so `Blob`/`btoa` behave as in the browser.
    environment: "node",
    globals: true,
  },
});
