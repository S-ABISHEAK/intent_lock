import { describe, expect, it } from "vitest";

import { layerV, promptGuard } from "../src/extraResults";

const v = (cases_run: number) => ({
  source: "x-v",
  summary: {
    summary: {
      cases_run, attacks: 36, attacks_stopped: 35, attacks_with_expected_code: 33,
      caught_only_by_dom_check: 2, twins: 18, twin_false_alarms: 1, twins_ordered: 17,
      vision_seconds_p50: 4.8,
    },
  },
});

describe("results page extras", () => {
  it("shows Layer V counts", () => {
    const r = layerV(v(54))!;
    expect(r.pilot).toBe(false);
    expect(r.rows[0]).toEqual(["Misleading pages stopped (no order placed)", "35/36"]);
  });

  it("labels a pilot as not a result", () => {
    const r = layerV(v(4))!;
    expect(r.pilot).toBe(true);
    expect(r.title).toContain("not a result");
  });

  it("shows nothing without results", () => {
    expect(layerV(null)).toBeNull();
    expect(promptGuard(undefined)).toBeNull();
  });

  it("compares stock and fine-tuned Prompt Guard", () => {
    const rows = promptGuard({
      summary: {
        splits: {
          test: {
            stock: { injections: 200, detected: 0, benign: 200, false_positives: 0 },
            finetuned: { injections: 200, detected: 180, benign: 200, false_positives: 0 },
          },
        },
      },
    })!;
    expect(rows[0]).toEqual({ split: "test", stock: "0/200 caught", finetuned: "180/200 caught" });
  });
});
