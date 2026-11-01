// The dev server is the only place the UI's secrets live (D-042). It adds X-Operator-Token to
// gateway calls and X-Approver-Token to approver calls, so it must refuse anything that is not
// this page talking to itself: another website (CSRF), a rebound DNS name, or a path the UI
// never uses (the agent's /mcp endpoint above all).

export interface Incoming {
  method?: string;
  url?: string;
  headers: Record<string, string | string[] | undefined>;
}

export const UI_HEADER = "x-intentlock-ui";

const GATEWAY_GET = [/^\/events(\?after=-?\d+)?$/, /^\/receipts$/, /^\/receipts\/verify$/, /^\/health$/];
const GATEWAY_POST = [
  /^\/mandate\/compile$/,
  /^\/mandate\/confirm$/,
  /^\/session$/,
  /^\/session\/end$/,
  /^\/approvals\/[A-Za-z0-9_-]{1,80}\/(signature|reject)$/,
];

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export function origins(port: number): string[] {
  return [`http://localhost:${port}`, `http://127.0.0.1:${port}`];
}

/** null if the request may go through the proxy, otherwise why not. */
export function refuse(req: Incoming, port: number): string | null {
  const url = req.url ?? "";
  // Match the way Vite's proxy picks requests (a raw prefix test, no slash needed) so nothing
  // can reach the proxy around this guard; only the slash forms can ever be allowed.
  if (!url.startsWith("/api") && !url.startsWith("/approver")) return null; // page assets
  const gateway = url.startsWith("/api/");
  const approver = url.startsWith("/approver/");
  if (!gateway && !approver) return "path not allowed";

  const host = one(req.headers.host);
  if (!host || !origins(port).includes(`http://${host}`)) return "unexpected host";
  const site = one(req.headers["sec-fetch-site"]);
  if (site !== undefined && site !== "same-origin") return "cross-site request";
  const origin = one(req.headers.origin);
  if (origin !== undefined && !origins(port).includes(origin)) return "foreign origin";

  const method = (req.method ?? "GET").toUpperCase();
  const path = url.slice(gateway ? "/api".length : "/approver".length);
  if (method === "POST") {
    // Browsers always send Origin on a POST from fetch; the custom header forces a CORS
    // preflight for any other site, which the dev server never approves.
    if (origin === undefined) return "missing origin";
    if (one(req.headers[UI_HEADER]) !== "1") return "missing UI header";
    if (gateway && GATEWAY_POST.some((r) => r.test(path))) return null;
    if (approver && path === "/sign") return null;
    return "path not allowed";
  }
  if (method === "GET" && gateway && GATEWAY_GET.some((r) => r.test(path))) return null;
  return "path not allowed";
}
