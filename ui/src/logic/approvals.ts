// What an approval card may offer, from facts only. The backend is authoritative for expiry: the
// local countdown only disables the buttons; it never claims a rejection.

export type Phase = "pending" | "signing" | "submitting" | "approved" | "rejected" | "failed";

export type CardAction =
  | { type: "approve" }
  | { type: "signed" }
  | { type: "reject" }
  | { type: "done"; result: "approved" | "rejected" }
  | { type: "error"; message: string };

export type CardState = { phase: Phase; error: string };

export const initialCard: CardState = { phase: "pending", error: "" };

export function cardReducer(s: CardState, a: CardAction): CardState {
  switch (a.type) {
    case "approve":
      return s.phase === "pending" || s.phase === "failed" ? { phase: "signing", error: "" } : s;
    case "signed":
      return s.phase === "signing" ? { phase: "submitting", error: "" } : s;
    case "reject":
      return s.phase === "pending" || s.phase === "failed" ? { phase: "submitting", error: "" } : s;
    case "done":
      return { phase: a.result, error: "" };
    case "error":
      return { phase: "failed", error: a.message };
  }
}

export type CardView = {
  secondsLeft: number;
  canAct: boolean;
  notice: string | null;
};

export function cardView(p: { now: number; expiresAt: number; fresh: boolean; phase: Phase; complete?: boolean }): CardView {
  const secondsLeft = Math.max(0, Math.floor(p.expiresAt - p.now));
  const idle = p.phase === "pending" || p.phase === "failed";
  let notice: string | null = null;
  const complete = p.complete !== false;
  if (!p.fresh) notice = "Live verification unavailable: reconnect to act on this";
  else if (!complete) notice = "The gateway's summary for this card is missing: it can't be approved here";
  else if (secondsLeft === 0 && idle) notice = "Expiry reached: verifying gateway status";
  return { secondsLeft, canAct: idle && p.fresh && complete && secondsLeft > 0, notice };
}

// Freshness: after a disconnect, cards stay non-actionable until the gateway sends a new pending
// list on the new connection (it always does on connect), so nothing stale looks actionable.
export type Fresh = { live: boolean; fresh: boolean };
export type FreshEvent = { type: "live"; live: boolean } | { type: "pending" };

export function freshReducer(s: Fresh, e: FreshEvent): Fresh {
  if (e.type === "live") return e.live ? { live: true, fresh: s.live && s.fresh } : { live: false, fresh: false };
  return { live: s.live, fresh: s.live };
}
