import type { Receipt } from "../api";

export type Mode = { label: string; tone: "protected" | "baseline" | "unprotected" | "unknown" };

export function modeLabel(config: string | null | undefined): Mode {
  if (config === "intentlock") return { label: "IntentLock", tone: "protected" };
  if (config === "r2_session") return { label: "Session Rule of Two (baseline)", tone: "baseline" };
  if (config === "none") return { label: "No protection (baseline)", tone: "unprotected" };
  return { label: "Unknown mode", tone: "unknown" };
}

export type SessionState =
  | { state: "none" }
  | { state: "ended"; sessionId: string; ts: number }
  | {
      state: "active";
      sessionId: string;
      ts: number;
      config: string | null;
      twoChannel: string | null;
      promptGuard: string | null;
    };

/** From the latest `mandate` receipt only. */
export function sessionState(receipts: Receipt[]): SessionState {
  for (let i = receipts.length - 1; i >= 0; i--) {
    const r = receipts[i];
    if (r.kind !== "mandate") continue;
    const p = r.payload ?? {};
    if (p.event === "ended") return { state: "ended", sessionId: String(p.session_id ?? ""), ts: r.ts };
    return {
      state: "active",
      sessionId: String(p.session_id ?? ""),
      ts: r.ts,
      config: typeof p.config === "string" ? p.config : null,
      twoChannel: typeof p.two_channel === "string" ? p.two_channel : null,
      promptGuard: typeof p.promptguard === "string" ? p.promptguard : null,
    };
  }
  return { state: "none" };
}

export type Connection = "connected" | "reconnecting" | "unavailable";

export function connection(live: boolean, healthOk: boolean | null): Connection {
  if (live && healthOk !== false) return "connected";
  return healthOk === false ? "unavailable" : "reconnecting";
}
