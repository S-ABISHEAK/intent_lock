import { describe, expect, it } from "vitest";

import type { Receipt } from "../src/api";
import { executionStatus, groupReceipts, policyStatus } from "../src/logic/stories";

const A = "a".repeat(32);
const B = "b".repeat(32);
let seq = 0;
const r = (kind: Receipt["kind"], payload: Record<string, any>): Receipt => ({
  seq: seq++,
  kind,
  payload,
  ts: 1,
  row_hash: "h",
});
const prop = (id: unknown) => r("proposal", { action_id: id, tool: "checkout", args: {} });
const dec = (id: string, outcome: string) => r("decision", { action_id: id, outcome, reasons: [] });
const fin = (id: string, executed: unknown, reasons: string[] = []) =>
  r("execution", { action_id: id, event: "finished", executed, reasons });

function one(rs: Receipt[]) {
  const g = groupReceipts(rs);
  expect(g.stories).toHaveLength(1);
  return g.stories[0];
}

describe("grouping by action_id only", () => {
  it("links proposal, decision, approvals and execution of one action", () => {
    const g = groupReceipts([
      r("mandate", { session_id: "s" }),
      r("tool_result", { tool: "get_product" }),
      prop(A),
      dec(A, "REVIEW"),
      r("approval", { event: "requested", action_id: A }),
      r("approval", { event: "signed", action_id: A }),
      r("execution", { action_id: A, event: "started" }),
      fin(A, true),
    ]);
    expect(g.stories).toHaveLength(1);
    const s = g.stories[0];
    expect(s.decision).not.toBeNull();
    expect(s.approvals).toHaveLength(2);
    expect(s.executions).toHaveLength(2);
    expect(g.unlinked).toHaveLength(0);
    expect(g.reads).toHaveLength(1);
    expect(g.sessions).toHaveLength(1);
  });

  it("two actions never merge, even when interleaved", () => {
    const g = groupReceipts([prop(A), prop(B), dec(B, "DENY"), dec(A, "ALLOW")]);
    expect(g.stories.map((s) => [s.id, policyStatus(s)])).toEqual([
      [A, "ALLOW"],
      [B, "DENY"],
    ]);
  });

  it.each([
    ["missing", undefined],
    ["empty", ""],
    ["short", "abc"],
    ["uppercase", "A".repeat(32)],
    ["non-string", 42],
  ])("a %s id is unlinked, never attached by position", (_n, id) => {
    const g = groupReceipts([prop(A), r("approval", { event: "signed", action_id: id }), r("decision", { action_id: id, outcome: "ALLOW" })]);
    expect(g.stories[0].approvals).toHaveLength(0);
    expect(g.stories[0].decision).toBeNull();
    expect(g.unlinked).toHaveLength(2);
  });

  it("an old approval receipt without an id stays unlinked", () => {
    const g = groupReceipts([prop(A), dec(A, "REVIEW"), r("approval", { event: "not_approved", approval_id: "x" })]);
    expect(g.unlinked.map((u) => u.kind)).toEqual(["approval"]);
    expect(executionStatus(g.stories[0])).toBe("PENDING");
  });

  it("an id with no proposal is an orphan, and a malformed proposal never forms a story", () => {
    const g = groupReceipts([prop("nope"), fin(B, true)]);
    expect(g.stories).toHaveLength(0);
    expect(g.unlinked).toHaveLength(2);
  });

  it("a duplicated proposal id is not merged", () => {
    const g = groupReceipts([prop(A), prop(A)]);
    expect(g.stories).toHaveLength(1);
    expect(g.unlinked).toHaveLength(1);
  });
});

describe("policy and execution are separate", () => {
  it("ALLOW with no execution receipt is PENDING, never EXECUTED", () => {
    const s = one([prop(A), dec(A, "ALLOW")]);
    expect([policyStatus(s), executionStatus(s)]).toEqual(["ALLOW", "PENDING"]);
  });
  it("ALLOW with an unknown outcome", () => {
    const s = one([prop(A), dec(A, "ALLOW"), fin(A, "unknown", ["EXECUTION_FAILED"])]);
    expect([policyStatus(s), executionStatus(s)]).toEqual(["ALLOW", "UNKNOWN"]);
  });
  it("ALLOW then refused", () => {
    const s = one([prop(A), dec(A, "ALLOW"), r("execution", { action_id: A, event: "refused", executed: false, reasons: ["QUOTE_CHANGED"] })]);
    expect([policyStatus(s), executionStatus(s)]).toEqual(["ALLOW", "REFUSED"]);
  });
  it("a merchant failure is FAILED, not refused", () => {
    const s = one([prop(A), dec(A, "ALLOW"), fin(A, false, ["EXECUTION_FAILED"])]);
    expect(executionStatus(s)).toBe("FAILED");
  });
  it("REVIEW then not approved is NOT_EXECUTED", () => {
    const s = one([prop(A), dec(A, "REVIEW"), r("approval", { event: "not_approved", action_id: A })]);
    expect([policyStatus(s), executionStatus(s)]).toEqual(["REVIEW", "NOT_EXECUTED"]);
  });
  it("DENY is NOT_EXECUTED", () => {
    const s = one([prop(A), dec(A, "DENY")]);
    expect([policyStatus(s), executionStatus(s)]).toEqual(["DENY", "NOT_EXECUTED"]);
  });
  it("EXECUTED only from executed: true", () => {
    expect(executionStatus(one([prop(A), dec(A, "ALLOW"), fin(A, true)]))).toBe("EXECUTED");
    expect(executionStatus(one([prop(A), dec(A, "ALLOW"), fin(A, "yes")]))).toBe("UNKNOWN");
    expect(executionStatus(one([prop(A), dec(A, "ALLOW"), r("execution", { action_id: A, event: "started" })]))).toBe("UNKNOWN");
  });
  it("a merchant call that started with no recorded end is UNKNOWN, never 'not executed'", () => {
    const s = one([prop(A), dec(A, "ALLOW"), r("execution", { action_id: A, event: "started" }), r("error", { action_id: A, error: "RuntimeError" })]);
    expect(executionStatus(s)).toBe("UNKNOWN");
  });
  it("no decision receipt means no policy status", () => {
    expect(policyStatus(one([prop(A)]))).toBeNull();
  });
});
