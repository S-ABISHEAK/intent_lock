import { useEffect, useState } from "react";

import type { Card, Receipt } from "./api";

// The gateway's SSE stream (SPEC §3): receipts in order, and the pending approval cards.
// EventSource reconnects by itself; on reconnect we ask only for receipts after the last seq.
export function useEvents() {
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [pending, setPending] = useState<Card[]>([]);
  const [live, setLive] = useState(false);

  useEffect(() => {
    let last = -1;
    let source: EventSource | null = null;
    let timer: number | undefined;
    const open = () => {
      source = new EventSource(`/api/events?after=${last}`);
      source.onopen = () => setLive(true);
      source.addEventListener("receipt", (e) => {
        const r = JSON.parse((e as MessageEvent).data) as Receipt;
        if (r.seq <= last) return;
        last = r.seq;
        setReceipts((rs) => [...rs, r]);
      });
      source.addEventListener("pending", (e) => setPending(JSON.parse((e as MessageEvent).data)));
      source.onerror = () => {
        setLive(false);
        source?.close();
        timer = window.setTimeout(open, 1500); // reopen with the current cursor
      };
    };
    open();
    return () => {
      window.clearTimeout(timer);
      source?.close();
    };
  }, []);

  return { receipts, pending, live };
}
