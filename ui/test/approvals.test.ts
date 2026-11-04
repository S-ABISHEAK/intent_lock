import { describe, expect, it } from "vitest";

import { cardReducer, cardView, freshReducer, initialCard } from "../src/logic/approvals";

describe("approval card state", () => {
  it("approve goes signing -> submitting -> approved", () => {
    let s = cardReducer(initialCard, { type: "approve" });
    expect(s.phase).toBe("signing");
    s = cardReducer(s, { type: "signed" });
    expect(s.phase).toBe("submitting");
    s = cardReducer(s, { type: "done", result: "approved" });
    expect(s.phase).toBe("approved");
  });

  it("a second click while busy does nothing", () => {
    const s = cardReducer(initialCard, { type: "approve" });
    expect(cardReducer(s, { type: "approve" })).toBe(s);
    expect(cardReducer(s, { type: "reject" })).toBe(s);
  });

  it("a backend error is shown verbatim and the card can be retried", () => {
    const s = cardReducer(cardReducer(initialCard, { type: "approve" }), { type: "error", message: "409: APPROVAL_EXPIRED" });
    expect(s).toEqual({ phase: "failed", error: "409: APPROVAL_EXPIRED" });
    expect(cardReducer(s, { type: "approve" }).phase).toBe("signing");
  });
});

describe("cardView", () => {
  it("is actionable while fresh and before expiry", () => {
    expect(cardView({ now: 100, expiresAt: 160, fresh: true, phase: "pending" })).toEqual({ secondsLeft: 60, canAct: true, notice: null });
  });

  it("at 0 s it disables actions and claims no rejection", () => {
    const v = cardView({ now: 160, expiresAt: 160, fresh: true, phase: "pending" });
    expect(v.canAct).toBe(false);
    expect(v.notice).toBe("Expiry reached: verifying gateway status");
    expect(v.notice).not.toMatch(/reject/i);
  });

  it("a stale (disconnected) card is never actionable", () => {
    const v = cardView({ now: 100, expiresAt: 160, fresh: false, phase: "pending" });
    expect(v.canAct).toBe(false);
    expect(v.notice).toMatch(/reconnect/i);
  });

  it("a card being submitted is not actionable", () => {
    expect(cardView({ now: 100, expiresAt: 160, fresh: true, phase: "submitting" }).canAct).toBe(false);
  });
});

describe("freshness after a disconnect", () => {
  it("reconnecting alone does not make cards fresh; the next pending list does", () => {
    let s = { live: true, fresh: true };
    s = freshReducer(s, { type: "live", live: false });
    expect(s).toEqual({ live: false, fresh: false });
    s = freshReducer(s, { type: "pending" });
    expect(s.fresh).toBe(false); // a pending list while offline cannot happen, and proves nothing
    s = freshReducer(s, { type: "live", live: true });
    expect(s.fresh).toBe(false);
    s = freshReducer(s, { type: "pending" });
    expect(s).toEqual({ live: true, fresh: true });
  });
});
