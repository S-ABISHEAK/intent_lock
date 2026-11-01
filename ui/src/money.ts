// Money is integer paise everywhere (invariant 4); this only formats it for display.
export function rupees(paise: number): string {
  if (!Number.isSafeInteger(paise)) return "₹?";
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const whole = Math.floor(abs / 100).toLocaleString("en-IN");
  const frac = String(abs % 100).padStart(2, "0");
  return `${sign}₹${whole}.${frac}`;
}
