// Fail-safe parser for the provenance detail on TAINTED_ARG reasons. It accepts only the two
// formats intentlock/provenance/taint.py writes:
//   <field>=<value> sources=['<id>', ...] via <chain>[; <chain>]*
//   <field>=<value> is also named by untrusted content ['<id>', ...] via <chain>[; <chain>]*
// where <chain> is <step>( -> <step>)* and <step> is `<tool>#<int> <field_path>`.
// Anything else returns null: the UI then shows the original reason text and no graph. Nothing is
// repaired or inferred. Steps are labelled neutrally (the backend orders each chain from the
// earliest record to the one the value was read from; it does not label their roles).

export type Step = { tool: string; seq: number; path: string };
export type Chain = {
  kind: "sources" | "also_named";
  field: string;
  value: string;
  sources: string[];
  chains: Step[][];
};

const HEAD = /^([A-Za-z_][\w.[\]]*)=(.*) (sources=|is also named by untrusted content )\[([^\]]*)\] via (.+)$/s;
const STEP = /^([a-z_][a-z0-9_]*)#(\d+) (\S+)$/;
const SOURCE = /^'([^']*)'$/;

function parseSources(list: string): string[] | null {
  if (list.trim() === "") return [];
  const out: string[] = [];
  for (const part of list.split(", ")) {
    const m = SOURCE.exec(part);
    if (!m) return null;
    out.push(m[1]);
  }
  return out;
}

export function parseChain(detail: unknown): Chain | null {
  if (typeof detail !== "string") return null;
  const m = HEAD.exec(detail);
  if (!m) return null;
  const sources = parseSources(m[4]);
  if (sources === null) return null;
  const chains: Step[][] = [];
  for (const raw of m[5].split("; ")) {
    const steps: Step[] = [];
    for (const s of raw.split(" -> ")) {
      const sm = STEP.exec(s);
      if (!sm) return null;
      steps.push({ tool: sm[1], seq: Number(sm[2]), path: sm[3] });
    }
    chains.push(steps);
  }
  return {
    kind: m[3] === "sources=" ? "sources" : "also_named",
    field: m[1],
    value: m[2],
    sources,
    chains,
  };
}
