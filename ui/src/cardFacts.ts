// What an approval card must show beyond the quote table (checkpoint review MED-2): the exact
// items a share sends, since the approval signs their hash (D-051), and which lines are
// refurbished (D-050). Pure, so it is unit-tested.
export type CardFacts = {
  shareTo: string | null;
  shareItems: string[] | null;
  refurbished: string[];
};

export function cardFacts(s: any): CardFacts {
  const share = s?.tool === "share_cart" && Array.isArray(s?.items);
  return {
    shareTo: share ? String(s.args?.to ?? "") : null,
    shareItems: share ? s.items.map((i: any) => `${i.sku} × ${i.qty}`) : null,
    refurbished: Array.isArray(s?.lines)
      ? s.lines.filter((l: any) => l.condition === "refurbished").map((l: any) => String(l.sku))
      : [],
  };
}

// The Checkout Truth Engine on a checkout card (D-059): channel 1 is the merchant's authoritative
// quote (the table itself), channel 2 is what the page shows (step 8: Muse vision cross-checked
// with the page's own text), channel 3 is the order confirmation, checked after approval. The
// screen is never reported as matching unless step 8 actually ran and raised nothing.
const SCREEN_CODES = new Set([
  "PRICE_DISPLAY_MISMATCH",
  "HIDDEN_LINE_ITEM",
  "PRESELECTED_ADDON",
  "RECURRING_MISMATCH",
  "NON_REFUNDABLE",
  "REFUND_POLICY_UNKNOWN",
  "QUANTITY_DISPLAY_MISMATCH",
  "ADDRESS_DISPLAY_MISMATCH",
  "VISION_DOM_DISAGREE",
]);

export type CheckoutTruth = {
  quote: "verified";
  screen: { state: "matches" | "flagged" | "off" | "unavailable"; codes: string[] };
  confirmation: string;
};

export function checkoutTruth(s: any): CheckoutTruth | null {
  if (s?.tool !== "checkout") return null;
  const reasons: string[] = Array.isArray(s.reasons) ? s.reasons : [];
  const codes = reasons.filter((c) => SCREEN_CODES.has(c));
  let state: CheckoutTruth["screen"]["state"];
  if (s.two_channel !== "on") state = "off";
  else if (reasons.includes("TWO_CHANNEL_UNAVAILABLE")) state = "unavailable";
  else state = codes.length ? "flagged" : "matches";
  return {
    quote: "verified",
    screen: { state, codes },
    confirmation: "checked against this quote after you approve",
  };
}
