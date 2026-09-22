/**
 * Emits a copy of the SPA shell (`index.html`) inside each client-side route's
 * directory in the build output, e.g. `dist/app/billing/index.html`.
 *
 * Why this exists
 * ---------------
 * `transform-web` is hosted as a Render **static site**, which only serves a
 * client-side route if a real file exists at that path. Render's documented way
 * to handle this is a catch-all rewrite (`/*` -> `/index.html`) configured in
 * the Dashboard, but that rule lives outside this repository and has to be
 * applied by hand (Render has no `_redirects` file support and the build cannot
 * set it).
 *
 * Until that rule exists, every non-root path 404s at the CDN — including deep
 * links, refreshes, and the redirect Stripe performs after a successful
 * Checkout (e.g. `/app/billing?credits=success`). A user whose card was charged
 * would land on a "Not found" page.
 *
 * Writing the shell to disk at each route makes those paths resolvable with no
 * dashboard access, so payment returns and deep links work regardless of the
 * host's route configuration.
 *
 * This is safe to keep once the rewrite rule is applied: Render only applies a
 * rewrite when no resource exists at the path, so these stubs simply take
 * precedence and serve byte-identical content. `assets/*` is unaffected because
 * those files still exist under their hashed names.
 *
 * IMPORTANT: keep `SPA_ROUTES` in sync with the route table in `src/App.tsx`.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import type { Plugin } from "vite";

/**
 * Client-side routes that must survive a hard load (direct navigation, refresh,
 * or an external redirect). `/` is omitted because `index.html` already serves it.
 */
export const SPA_ROUTES = [
  // Public marketing routes
  "/pricing",
  "/security",
  "/convert",
  // Auth routes
  "/login",
  "/register",
  // Landing page for the link in the verification email. Reached by a hard load
  // from a mail client, so it MUST have a stub or the link 404s at the CDN.
  "/verify-email",
  // Authenticated app routes
  "/app/dashboard",
  "/app/convert",
  "/app/queue",
  "/app/history",
  "/app/files",
  "/app/billing",
  "/app/settings",
  "/app/support",
] as const;

/**
 * Vite plugin: after the bundle is written, copy the built `index.html` into a
 * directory per entry in `routes`.
 */
export function spaRouteStubs(routes: readonly string[] = SPA_ROUTES): Plugin {
  let distDir = "";

  return {
    name: "spa-route-stubs",
    apply: "build",

    configResolved(config) {
      distDir = resolve(config.root, config.build.outDir);
    },

    async closeBundle() {
      const shell = await readFile(join(distDir, "index.html"));

      await Promise.all(
        routes.map(async (route) => {
          const target = join(distDir, route.replace(/^\/+/, ""), "index.html");
          await mkdir(dirname(target), { recursive: true });
          await writeFile(target, shell);
        }),
      );

      this.info(`spa-route-stubs: wrote SPA shell for ${routes.length} routes`);
    },
  };
}
