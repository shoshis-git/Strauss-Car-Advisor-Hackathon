// Typed client for the FastAPI backend (backend/api/routes.py).

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

export interface Side {
  label: string;
  kind: "car" | "waiver";
  car_id: string | null;
  is_electric: boolean;
  value_of_use: number;
  participation: number;
  maintenance_allowance: number;
  electric_gross: number;
  family_gap_deduction: number;
  income_tax: number;
  btl_health: number;
  net: number;
}

export interface Comparison {
  ok: boolean;
  is_eligible: boolean;
  reason: string;
  current_label: string;
  alternative_label: string;
  net_today: number;
  net_alt: number | null;
  net_diff: number | null;
  tax_today: number;
  tax_alt: number | null;
  btl_health_today: number;
  btl_health_alt: number | null;
  electric_gross: number | null;
  family_gap_deduction: number | null;
  explanation: string;
  today: Side;
  alternative: Side | null;
}

export interface Profile {
  eligibility_group: string | null;
  occupation: string | null;
  base_salary: number | null;
  commissions_bonuses: number | null;
  pension_pct: number | null;
  hishtalmut_pct: number | null;
  credit_points: number | null;
  children_count: number | null;
}

export interface StateView {
  display_name: string | null;
  profile: Profile;
  current: {
    status: "car" | "waiver" | null;
    current_car_code: string | null;
    current_participation: number | null;
    current_maintenance: number | null;
  };
  target: {
    target_action: "replace" | "waiver" | null;
    target_car_code: string | null;
    target_participation: number | null;
  };
  missing_info: string[];
  missing_labels: string[];
  waiting_for_rep: boolean;
  rep_question: string | null;
  last_comparison: Comparison | null;
  /** fail-fast eligibility of the request, known before any salary data */
  precheck?: Precheck | null;
}

export interface Precheck {
  eligible: boolean;
  requested: string;
  reason: string;
  alternatives?: CarSummary[];
}

export interface ChatResponse {
  session_id: string;
  bot_message: string;
  state: StateView;
  comparison: Comparison | null;
  frozen: boolean;
  /** the turn was answered by the deterministic fallback because the LLM is unavailable */
  llm_fallback?: boolean;
  notice?: string | null;
}

export type Role = "employee" | "assistant" | "representative";

export interface Message {
  role: Role;
  content: string;
  created_at: string;
}

export interface SessionResponse {
  session_id: string;
  state: StateView;
  messages: Message[];
  created_at: string;
  updated_at: string;
}

export interface CarSummary {
  car_id: string;
  model_name: string;
  manufacturer: string;
  group: string;
  drive: string;
  registration_year: number;
  value_of_use: number;
  default_participation: number;
}

export interface Options {
  ok: boolean;
  error?: string;
  group?: string;
  budget?: number;
  allowed_cars: CarSummary[];
  family_track: { eligible: boolean | null; cars: CarSummary[]; reason: string | null };
  waiver: { allowed: boolean | null; reason: string; maintenance?: number };
}

export interface DemoEmployee {
  employee_id: string;
  name: string;
  occupation: string;
  question: string;
}

export interface Catalog {
  demo_employees: DemoEmployee[];
  cars: CarSummary[];
  occupations: { name: string; eligibility_group: string }[];
  groups: { group: string; budget: number }[];
}

export interface CarView {
  car_id: string;
  model_name: string;
  group: string;
  drive: string;
  default_participation: number;
}

export interface Ticket {
  session_id: string;
  employee_name: string | null;
  rep_question: string;
  rep_field: string | null;
  waiting_since: string;
  profile: Profile;
  current_car: CarView | null;
  current_participation: number | null;
  target_action: string | null;
  target_car: CarView | null;
  last_employee_message: string | null;
}

export interface RepAnswerResponse {
  ok: boolean;
  session_id: string;
  employee_name: string | null;
  bot_message: string;
  comparison: Comparison | null;
  waiting_for_rep: boolean;
}

// Representative key (REP_API_KEY on the server), entered once on the rep screen and kept
// for this browser tab only.
const REP_KEY_STORAGE = "car-advisor.rep-key";

export function getRepKey(): string | null {
  try {
    return sessionStorage.getItem(REP_KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setRepKey(key: string | null) {
  try {
    if (key) sessionStorage.setItem(REP_KEY_STORAGE, key);
    else sessionStorage.removeItem(REP_KEY_STORAGE);
  } catch {
    /* storage unavailable - the key must be re-entered */
  }
}

const repHeaders = (): Record<string, string> => ({ "X-Rep-Token": getRepKey() ?? "" });

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, `השרת אינו זמין (${API_BASE}). האם ה-backend רץ?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
  });

export const api = {
  catalog: () => request<Catalog>("/api/catalog"),
  /** sessionId null = first message: the server opens the conversation and returns its id */
  chat: (message: string, sessionId: string | null) =>
    post<ChatResponse>("/api/chat", sessionId ? { session_id: sessionId, message } : { message }),
  session: (id: string) => request<SessionResponse>(`/api/session/${id}`),
  /** always a NEW conversation with a server-generated id */
  loadDemo: (empId: string) => post<ChatResponse>(`/api/demo/${empId}`),
  options: (id: string) => request<Options>(`/api/session/${id}/options`),
  setAlternative: (id: string, choice: { car_id: string } | { waiver: true }) =>
    post<ChatResponse>(`/api/session/${id}/alternative`, choice),
  tickets: () => request<Ticket[]>("/api/rep/tickets", { headers: repHeaders() }),
  answer: (id: string, answer_text: string, participation?: number) =>
    post<RepAnswerResponse>(`/api/rep/tickets/${id}/answer`, { answer_text, participation }, repHeaders()),
};
