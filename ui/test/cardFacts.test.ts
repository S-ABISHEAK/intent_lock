// Checkpoint review MED-2: the card must show what the approval signs (D-051 share contents)
// and which line is refurbished (D-050), not only reason codes.
import { describe, expect, it } from "vitest";

import { cardFacts, checkoutTruth, confirmationEvidence } from "../src/cardFacts";

describe("cardFacts", () => {
  it("lists the exact items a share will send, with the recipient", () => {
    const f = cardFacts({
      tool: "share_cart",
      args: { cart_id: "c1", to: "asha@example.com" },
      items: [
        { sku: "MS-1001", qty: 1 },
        { sku: "KB-2001", qty: 2 },
      ],
      reasons: ["APPROVAL_REQUIRED"],
    });
    expect(f.shareTo).toBe("asha@example.com");
    expect(f.shareItems).toEqual(["MS-1001 × 1", "KB-2001 × 2"]);
  });

  it("says when a share sends an empty cart", () => {
    expect(cardFacts({ tool: "share_cart", args: { to: "a@b.co" }, items: [] }).shareItems).toEqual([]);
  });

  it("marks refurbished lines", () => {
    const f = cardFacts({
      tool: "checkout",
      lines: [
        { sku: "LAP-1202", condition: "refurbished" },
        { sku: "MS-1001", condition: "new" },
      ],
    });
    expect(f.refurbished).toEqual(["LAP-1202"]);
  });

  it("is empty for a summary without these fields", () => {
    expect(cardFacts({})).toEqual({ shareTo: null, shareItems: null, refurbished: [] });
  });
});

describe("checkoutTruth", () => {
  it("is null for anything but a checkout", () => {
    expect(checkoutTruth({ tool: "share_cart" })).toBeNull();
  });

  it("says the screen matched when step 8 ran and raised nothing", () => {
    const t = checkoutTruth({ tool: "checkout", two_channel: "on", reasons: ["APPROVAL_REQUIRED"] })!;
    expect(t.quote).toBe("verified");
    expect(t.screen).toEqual({ state: "matches", codes: [] });
  });

  it("lists what the screen check flagged", () => {
    const t = checkoutTruth({
      tool: "checkout",
      two_channel: "on",
      reasons: ["APPROVAL_REQUIRED", "VISION_DOM_DISAGREE", "PRICE_DISPLAY_MISMATCH"],
    })!;
    expect(t.screen).toEqual({ state: "flagged", codes: ["VISION_DOM_DISAGREE", "PRICE_DISPLAY_MISMATCH"] });
  });

  it("never claims a match when the check is off or failed", () => {
    expect(checkoutTruth({ tool: "checkout", two_channel: "off", reasons: [] })!.screen.state).toBe("off");
    const failed = checkoutTruth({ tool: "checkout", two_channel: "on", reasons: ["TWO_CHANNEL_UNAVAILABLE"] })!;
    expect(failed.screen.state).toBe("unavailable");
    expect(checkoutTruth({ tool: "checkout", reasons: [] })!.screen.state).toBe("off");
  });
});

describe("confirmationEvidence (channel 3)", () => {
  it("before execution, says when it will be checked", () => {
    expect(confirmationEvidence("PENDING", [], null)).toEqual({ tone: "neutral", text: "Checked against this quote after you approve" });
  });
  it("executed: the merchant's order, never a verified payment", () => {
    const e = confirmationEvidence("EXECUTED", [], null);
    expect(e.tone).toBe("allow");
    expect(e.text).toBe("Order confirmed by the merchant");
    expect(e.note).toBe("Payment settlement not independently verified");
  });
  it("unknown is never green and names the reconciliation", () => {
    const e = confirmationEvidence("UNKNOWN", ["EXECUTION_FAILED"], "NOT_PLACED");
    expect(e.tone).toBe("review");
    expect(e.text).toBe("Outcome unknown");
    expect(e.note).toContain("NOT_PLACED");
  });
  it("refused names the code", () => {
    expect(confirmationEvidence("REFUSED", ["QUOTE_CHANGED"], null)).toEqual({ tone: "deny", text: "Refused: QUOTE_CHANGED" });
  });
  it("not executed", () => {
    expect(confirmationEvidence("NOT_EXECUTED", [], null).text).toBe("No order: the action was not executed");
  });
});
