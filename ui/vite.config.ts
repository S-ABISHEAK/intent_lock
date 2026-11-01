import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

import { refuse } from "./server/guard";

// Secrets are read here, in the dev server process, and only ever added to proxied requests
// (D-042). They are never passed to `define` or exposed with a VITE_ prefix, so the bundle the
// browser loads cannot contain them (test/bundle.test.ts builds it and checks).
const PORT = 5173;
const GATEWAY = process.env.GATEWAY_URL || "http://127.0.0.1:8000";
const APPROVER = process.env.APPROVER_URL || "http://127.0.0.1:8002";

function guard(): Plugin {
  return {
    name: "intentlock-guard",
    configureServer(server) {
      const mw: Connect.NextHandleFunction = (req, res, next) => {
        const why = refuse(req, PORT);
        if (why) {
          res.statusCode = 403;
          res.end(why);
          return;
        }
        next();
      };
      server.middlewares.use(mw); // runs before Vite's own proxy middleware
    },
  };
}

export default defineConfig({
  plugins: [guard(), react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: PORT,
    strictPort: true,
    cors: false,
    proxy: {
      "/api": {
        target: GATEWAY,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
        headers: { "X-Operator-Token": process.env.GATEWAY_OPERATOR_TOKEN ?? "" },
      },
      "/approver": {
        target: APPROVER,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/approver/, ""),
        headers: { "X-Approver-Token": process.env.APPROVER_UI_TOKEN ?? "" },
      },
    },
  },
});
