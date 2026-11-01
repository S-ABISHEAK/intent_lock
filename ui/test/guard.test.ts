import { describe, expect, it } from "vitest";

import { refuse } from "../server/guard";

const PORT = 5173;
const SAME = { host: "localhost:5173", origin: "http://localhost:5173", "sec-fetch-site": "same-origin", "x-intentlock-ui": "1" };

function req(method: string, url: string, headers: Record<string, string | undefined> = SAME) {
  return { method, url, headers };
}

describe("the dev-server guard in front of the token-injecting proxy", () => {
  it("lets the page's own calls through", () => {
    for (const [m, u] of [
      ["GET", "/api/events"],
      ["GET", "/api/events?after=12"],
      ["GET", "/api/receipts/verify"],
      ["POST", "/api/mandate/compile"],
      ["POST", "/api/mandate/confirm"],
      ["POST", "/api/session"],
      ["POST", "/api/approvals/apr_123-x/signature"],
      ["POST", "/api/approvals/apr_123-x/reject"],
      ["POST", "/approver/sign"],
    ]) {
      expect(refuse(req(m, u), PORT), `${m} ${u}`).toBeNull();
    }
  });

  it("refuses another website, even with the UI header", () => {
    const evil = { ...SAME, origin: "https://evil.example", "sec-fetch-site": "cross-site" };
    expect(refuse(req("POST", "/approver/sign", evil), PORT)).toBe("cross-site request");
    expect(refuse(req("GET", "/api/receipts", evil), PORT)).toBe("cross-site request");
    const noSite = { ...SAME, origin: "https://evil.example", "sec-fetch-site": undefined };
    expect(refuse(req("POST", "/api/session", noSite), PORT)).toBe("foreign origin");
    const sameSite = { ...SAME, origin: "http://localhost:8001", "sec-fetch-site": "same-site" };
    expect(refuse(req("POST", "/approver/sign", sameSite), PORT)).toBe("cross-site request");
  });

  it("refuses a rebound DNS name", () => {
    const rebound = { ...SAME, host: "attacker.example:5173" };
    expect(refuse(req("GET", "/api/receipts", rebound), PORT)).toBe("unexpected host");
    expect(refuse(req("GET", "/api/receipts", { ...SAME, host: undefined }), PORT)).toBe("unexpected host");
  });

  it("needs Origin and the UI header on every POST", () => {
    expect(refuse(req("POST", "/approver/sign", { ...SAME, origin: undefined }), PORT)).toBe("missing origin");
    expect(refuse(req("POST", "/approver/sign", { ...SAME, "x-intentlock-ui": undefined }), PORT)).toBe("missing UI header");
  });

  it("never proxies paths the UI does not use", () => {
    for (const [m, u] of [
      ["POST", "/api/mcp"],
      ["GET", "/api/mcp"],
      ["GET", "/api/approvals/pending"],
      ["GET", "/approver/public_key"],
      ["GET", "/approver/sign"],
      ["POST", "/approver/sign/../x"],
      ["POST", "/api/session/../mcp"],
      ["DELETE", "/api/session"],
      ["GET", "/api/events?after=1&x=2"],
    ]) {
      expect(refuse(req(m, u), PORT), `${m} ${u}`).toBe("path not allowed");
    }
  });

  it("leaves the page's own assets alone", () => {
    expect(refuse(req("GET", "/src/main.tsx", {}), PORT)).toBeNull();
  });
});
