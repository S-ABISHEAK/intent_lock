import { type ReactNode, useEffect, useRef } from "react";

// Inline SVG icons (no icon dependency). Decorative: the word next to them carries the meaning.
const PATHS: Record<string, string> = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  hand: "M8 13V5.5a1.5 1.5 0 013 0V11m0-6.5a1.5 1.5 0 013 0V11m0-4.5a1.5 1.5 0 013 0V14a6 6 0 01-6 6h-1a6 6 0 01-5.2-3L3.5 13.5a1.5 1.5 0 012.6-1.5L8 14",
  shieldX: "M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3zM9.5 9.5l5 5m0-5l-5 5",
  question: "M9.5 9a2.5 2.5 0 114 2c-.9.6-1.5 1.1-1.5 2.2M12 17h.01",
  clock: "M12 7v5l3 2M12 3a9 9 0 110 18 9 9 0 010-18z",
  minus: "M7 12h10",
  x: "M7 7l10 10M17 7L7 17",
  alert: "M12 4l9 16H3l9-16zM12 10v4M12 17h.01",
  lock: "M7 11V8a5 5 0 0110 0v3M6 11h12v9H6z",
  link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1",
  chevron: "M9 6l6 6-6 6",
  receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2V3zM9 8h6M9 12h6",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 5v6h-6",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zm10-3a3 3 0 110 6 3 3 0 010-6z",
  arrow: "M5 12h14m-5-5l5 5-5 5",
};

export function Icon({ name, className = "h-4 w-4" }: { name: keyof typeof PATHS | string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`shrink-0 ${className}`} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.minus} />
    </svg>
  );
}

const NOTICE: Record<string, string> = {
  info: "border-primary/30 bg-primary-soft text-ink",
  review: "border-review/40 bg-review-soft text-ink",
  deny: "border-deny/40 bg-deny-soft text-ink",
  neutral: "border-line bg-sunken text-ink",
};
const NOTICE_ICON: Record<string, string> = { info: "eye", review: "alert", deny: "alert", neutral: "minus" };

export function Notice({ tone = "info", title, children, action }: { tone?: keyof typeof NOTICE; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role={tone === "deny" ? "alert" : "status"} className={`flex flex-wrap items-start gap-3 rounded-lg border p-3 text-sm ${NOTICE[tone]}`}>
      <Icon name={NOTICE_ICON[tone]} className={`mt-0.5 h-4 w-4 ${tone === "deny" ? "text-deny" : tone === "review" ? "text-review" : "text-primary"}`} />
      <div className="min-w-0 flex-1">
        <div className="font-semibold">{title}</div>
        {children && <div className="mt-0.5 text-muted">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-surface px-4 py-8 text-center">
      <div className="font-semibold">{title}</div>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="h-3 animate-pulse rounded bg-neutral-soft" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

/** A confirm dialog: focus starts on Cancel, Esc cancels, Tab stays inside, no default action. */
export function Dialog({ title, children, confirm, danger, onConfirm, onCancel }: {
  title: string;
  children: ReactNode;
  confirm: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Tab" && box.current) {
        const f = [...box.current.querySelectorAll<HTMLElement>("button")];
        const i = f.indexOf(document.activeElement as HTMLElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length]?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby="dlg-title" className="card w-full max-w-md p-5 shadow-xl">
        <h2 id="dlg-title" className="text-lg font-semibold">{title}</h2>
        <div className="mt-2 text-sm text-muted">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" ref={cancel} className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button type="button" className={danger ? "btn-danger" : "btn"} onClick={onConfirm}>{confirm}</button>
        </div>
      </div>
    </div>
  );
}
