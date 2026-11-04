// Layer V (Checkout Truth Engine, D-059) and the Prompt Guard fine-tune (D-046) on the results
// page. Pure, so it is unit-tested; a pilot is always labelled as a pilot, never as the result.
export const LAYER_V_CASES = 54;

export type Row = [string, string];

function frac(k: number, n: number) {
  return n ? `${k}/${n}` : "–";
}

export function layerV(v: any): { title: string; pilot: boolean; rows: Row[] } | null {
  const s = v?.summary?.summary;
  if (!s) return null;
  const pilot = s.cases_run < LAYER_V_CASES;
  return {
    title: pilot ? `Pilot (${s.cases_run} of ${LAYER_V_CASES} cases), not a result` : `${s.cases_run} cases`,
    pilot,
    rows: [
      ["Misleading pages stopped (no order placed)", frac(s.attacks_stopped, s.attacks)],
      ["…with the expected code", frac(s.attacks_with_expected_code, s.attacks)],
      ["…caught only by the page-text cross-check", String(s.caught_only_by_dom_check)],
      ["Honest pages with a false alarm", frac(s.twin_false_alarms, s.twins)],
      ["Honest pages bought with no card", frac(s.twins_ordered, s.twins)],
      ["Vision latency p50", s.vision_seconds_p50 == null ? "–" : `${s.vision_seconds_p50} s`],
    ],
  };
}

export function promptGuard(pg: any): { split: string; stock: string; finetuned: string }[] | null {
  const splits = pg?.summary?.splits;
  if (!splits) return null;
  const cell = (m: any) =>
    m.injections ? `${m.detected}/${m.injections} caught` : `${m.false_positives}/${m.benign} false positives`;
  return Object.entries(splits).map(([split, m]: [string, any]) => ({
    split,
    stock: cell(m.stock),
    finetuned: cell(m.finetuned),
  }));
}
