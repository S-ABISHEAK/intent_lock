import { readdirSync, readFileSync, statSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { build } from "vite";
import { expect, it } from "vitest";

const OPERATOR = "SENTINEL-OPERATOR-TOKEN-5e1f";
const APPROVER = "SENTINEL-APPROVER-TOKEN-9a7c";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

it("the built bundle contains neither token (invariant 8, D-042)", async () => {
  process.env.GATEWAY_OPERATOR_TOKEN = OPERATOR;
  process.env.APPROVER_UI_TOKEN = APPROVER;
  process.env.VITE_GATEWAY_OPERATOR_TOKEN = OPERATOR; // even a VITE_-prefixed copy must not leak
  const out = mkdtempSync(join(tmpdir(), "intentlock-ui-"));
  await build({ logLevel: "silent", build: { outDir: out, emptyOutDir: true } });
  const all = files(out);
  expect(all.length).toBeGreaterThan(1);
  for (const f of all) {
    const text = readFileSync(f, "latin1");
    expect(text.includes(OPERATOR), f).toBe(false);
    expect(text.includes(APPROVER), f).toBe(false);
  }
}, 120_000);
