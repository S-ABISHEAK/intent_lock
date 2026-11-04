import { describe, expect, it } from "vitest";

import type { Receipt } from "../src/api";
import { connection, modeLabel, sessionState } from "../src/logic/shell";

const m = (payload: Record<string, any>, ts = 5): Receipt => ({ seq: 0, kind: "mandate", payload, ts, row_hash: "h" });

describe("shell", () => {
  it("labels every mode", () => {
    expect(modeLabel("intentlock")).toEqual({ label: "IntentLock", tone: "protected" });
    expect(modeLabel("r2_session").label).toBe("Session Rule of Two (baseline)");
    expect(modeLabel("none")).toEqual({ label: "No protection (baseline)", tone: "unprotected" });
    expect(modeLabel(undefined).tone).toBe("unknown");
  });

  it("session status comes from the latest mandate receipt only", () => {
    expect(sessionState([]).state).toBe("none");
    const started = m({ session_id: "s1", config: "intentlock", two_channel: "on", promptguard: "off" });
    expect(sessionState([started])).toEqual({
      state: "active",
      sessionId: "s1",
      ts: 5,
      config: "intentlock",
      twoChannel: "on",
      promptGuard: "off",
    });
    expect(sessionState([started, m({ session_id: "s1", event: "ended" }, 9)])).toEqual({ state: "ended", sessionId: "s1", ts: 9 });
  });

  it("the stale state when disconnected", () => {
    expect(connection(true, true)).toBe("connected");
    expect(connection(true, null)).toBe("connected");
    expect(connection(false, null)).toBe("reconnecting");
    expect(connection(false, false)).toBe("unavailable");
    expect(connection(true, false)).toBe("unavailable");
  });
});
