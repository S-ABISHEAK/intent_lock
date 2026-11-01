// The guard against the real Vite dev server and proxy (Nov 3 review HIGH): the unit test of
// refuse() alone missed that Vite's proxy matches "/api" without a slash. Upstreams are echo
// servers; the test asserts what actually reaches them, and with which token.
import { createServer as http, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

const OPERATOR = "SENTINEL-OPERATOR-PROXY";
const APPROVER = "SENTINEL-APPROVER-PROXY";
let vite: ViteDevServer;
let base = "";
const seen: { who: string; url: string; op?: string; ap?: string }[] = [];
const upstreams: Server[] = [];

function echo(who: string): Promise<string> {
  return new Promise((resolve) => {
    const s = http((req: IncomingMessage, res) => {
      seen.push({ who, url: req.url ?? "", op: req.headers["x-operator-token"] as string, ap: req.headers["x-approver-token"] as string });
      res.end("{}");
    });
    upstreams.push(s);
    s.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${(s.address() as AddressInfo).port}`));
  });
}

beforeAll(async () => {
  process.env.GATEWAY_URL = await echo("gateway");
  process.env.APPROVER_URL = await echo("approver");
  process.env.GATEWAY_OPERATOR_TOKEN = OPERATOR;
  process.env.APPROVER_UI_TOKEN = APPROVER;
  process.env.UI_PORT = "5199"; // not 5173, so this runs while `make dev` is up
  vite = await createServer({ logLevel: "silent" });
  await vite.listen();
  base = "http://127.0.0.1:5199";
}, 60_000);

afterAll(async () => {
  await vite?.close();
  upstreams.forEach((s) => s.close());
});

const SAME = { Origin: "http://127.0.0.1:5199", "Sec-Fetch-Site": "same-origin", "X-IntentLock-UI": "1" };
const EVIL = { Origin: "http://evil.example", "Sec-Fetch-Site": "cross-site", "Content-Type": "text/plain" };

async function send(method: string, path: string, headers: Record<string, string>) {
  seen.length = 0;
  const res = await fetch(base + path, { method, headers, body: method === "POST" ? "x" : undefined });
  await res.text();
  return { status: res.status, upstream: [...seen] };
}

it("never forwards a cross-site or off-list request, slash or not", async () => {
  for (const [m, p] of [
    ["POST", "/apisession/end"],
    ["POST", "/apiapprovals/x/reject"],
    ["GET", "/apimcp"],
    ["GET", "/apiapprovals/pending"],
    ["GET", "/api%2fmcp"],
    ["POST", "/approversign"],
    ["OPTIONS", "/approversign"],
    ["POST", "/api/session/end"],
    ["POST", "/approver/sign"],
    ["GET", "/api/mcp"],
  ]) {
    const r = await send(m, p, EVIL);
    expect(r.upstream, `${m} ${p}`).toEqual([]);
    expect(r.status, `${m} ${p}`).toBeGreaterThanOrEqual(400);
  }
  for (const [m, p] of [["GET", "/apimcp"], ["POST", "/approversign"], ["GET", "/api/mcp"]]) {
    expect((await send(m, p, SAME)).upstream, `${m} ${p} same-origin`).toEqual([]);
  }
});

it("forwards the page's own calls with only the right token", async () => {
  const g = await send("POST", "/api/session/end", SAME);
  expect(g.upstream).toEqual([{ who: "gateway", url: "/session/end", op: OPERATOR, ap: undefined }]);
  const a = await send("POST", "/approver/sign", SAME);
  expect(a.upstream).toEqual([{ who: "approver", url: "/sign", op: undefined, ap: APPROVER }]);
});
