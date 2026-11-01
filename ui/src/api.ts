// Every call goes through the dev server's proxy, which adds the secrets (D-042). The page
// itself never holds a token; it only sends the header the guard requires on POSTs.

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(typeof body === "string" ? body : `HTTP ${status}`);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-IntentLock-UI": "1", ...init?.headers },
  });
  const text = await res.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* plain-text error */
  }
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface ReadbackLine {
  kind: "grant" | "limit" | "not_granted" | "question" | string;
  text: string;
  span: string | null;
  field: string | null;
}

export interface Compiled {
  mandate: Record<string, unknown> & { user_text: string; auto_approve_under_paise: number };
  readback: ReadbackLine[];
  issues: { field: string; code: string; detail: string }[];
  clarifications: string[];
}

export interface Confirmed {
  mandate: Compiled["mandate"];
  confirm_auto_approve_paise: number | null;
  readback: ReadbackLine[];
}

export interface Receipt {
  seq: number;
  kind: "mandate" | "tool_result" | "proposal" | "decision" | "approval" | "execution" | "error";
  payload: Record<string, any>;
  ts: number;
  row_hash: string;
}

export interface ApprovalRequest {
  approval_id: string;
  action_tool: string;
  expires_at: number;
  [k: string]: unknown;
}

export interface Card {
  request: ApprovalRequest;
  summary: Record<string, any> | null;
}

export const api = {
  compile: (user_text: string, answers: { question: string; answer: string }[]) =>
    post<Compiled>("/api/mandate/compile", { user_text, answers }),
  confirm: (mandate: Compiled["mandate"], accepted: string[], typed_auto_approve: string) =>
    post<Confirmed>("/api/mandate/confirm", { mandate, accepted, typed_auto_approve }),
  startSession: (c: Confirmed) =>
    post<{ session_id: string; config: string }>("/api/session", {
      mandate: c.mandate,
      confirm_auto_approve_paise: c.confirm_auto_approve_paise,
    }),
  endSession: () => post("/api/session/end"),
  health: () => call<{ ok: boolean; config: string }>("/api/health"),
  verify: () => call<{ ok: boolean; head: string; bad_seq: number | null }>("/api/receipts/verify"),
  // The user's click: the approver signs, then the gateway gets the signature (SPEC §10).
  approve: async (req: ApprovalRequest) => {
    const signed = await post<Record<string, unknown>>("/approver/sign", req);
    return post(`/api/approvals/${encodeURIComponent(req.approval_id)}/signature`, signed);
  },
  reject: (id: string) => post(`/api/approvals/${encodeURIComponent(id)}/reject`),
};

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const b = e.body as any;
    const detail = b?.detail;
    if (typeof detail === "string") return `${e.status}: ${detail}`;
    if (detail?.error) return `${e.status}: ${detail.error}`;
    return `${e.status}: ${typeof b === "string" ? b : JSON.stringify(b)}`;
  }
  return String(e);
}
