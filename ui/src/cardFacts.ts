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
