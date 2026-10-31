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
  /** where the new car's participation comes from (null for a waiver) */
  alternative_participation_source?: "company_catalogue_default" | "approved_by_representative" | null;
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
  /** while waiting for the representative: today's net; the alternative follows the answer */
  today_preview?: { today: Side; alternative_label: string | null } | null;
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

export type PayslipFieldName =
  | "occupation"
  | "eligibility_group"
  | "base_salary"
  | "commissions_bonuses"
  | "pension_pct"
  | "hishtalmut_pct"
  | "credit_points"
  | "current_participation";

export interface PayslipField {
  field: PayslipFieldName;
  label: string;
  value: number | string;
  /** the payslip line (after redaction) the value was read from */
  evidence: string;
}

/** Proposed values only - nothing is saved until the employee confirms them. */
export interface PayslipExtraction {
  fields: PayslipField[];
  not_found: string[];
  rejected: string[];
  /** text = read from the PDF text and verified against it; image = read from the page
   *  image by the vision model (no independent text - see checks) */
  source: "text" | "image" | null;
  /** the payslip's own arithmetic, checked in code (image only) */
  checks: { ok: boolean; text: string }[];
  /** no readable text in the PDF - why; the employee may then agree to send its image */
  unreadable?: string | null;
  /** the server can read payslip images (PAYSLIP_VISION and an image-capable model) */
  vision_available?: boolean;
}

export type PayslipValues = Partial<Record<PayslipFieldName, number | string>>;

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

export interface RankedOption {
  rank: number;
  kind: "car" | "waiver";
  car_id: string | null;
  label: string;
  model_name: string;
  group: string | null;
  drive: string | null;
  family_track: boolean;
  is_electric: boolean;
  company_monthly_cost: number | null;
  value_of_use: number;
  participation: number;
  maintenance_allowance: number;
  family_gap_deduction: number;
  net_alt: number;
  net_diff: number;
  explanation: string;
  is_selected: boolean;
}

/** All my options: every permitted alternative, best net first (engine only). */
export interface Ranking {
  ok: boolean;
  missing_labels?: string[];
  error?: string;
  current_label?: string;
  net_today?: number;
  group?: string;
  budget?: number;
  count?: number;
  best?: RankedOption | null;
  best_electric?: RankedOption | null;
  options?: RankedOption[];
  notes?: string[];
}

export interface DemoEmployee {
  employee_id: string;
  name: string;
  occupation: string;
  /** demo accounts only (fictional people): the access code shown on the login screen */
  demo_code: string | null;
}

export type LoginRequest = { emp_id: string; code: string } | { guest: true };

export interface LoginResponse {
  token: string;
  token_type: "bearer";
  expires_at: number;
  session_id: string;
  kind: "employee" | "guest";
  employee_name: string | null;
  bot_message: string;
  state: StateView;
}

/** What the browser keeps after login (this tab only). */
export interface Auth {
  token: string;
  session_id: string;
  kind: "employee" | "guest";
  employee_name: string | null;
  expires_at: number;
}

export const authOf = (r: LoginResponse): Auth => ({
  token: r.token,
  session_id: r.session_id,
  kind: r.kind,
  employee_name: r.employee_name,
  expires_at: r.expires_at,
});

/** A row in "my conversations" (logged-in employees only). */
export interface ConversationSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  waiting_for_rep: boolean;
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

// Employee login token (signed by the server, bound to one conversation). Kept for this
// browser tab only: a refresh keeps the conversation, closing the tab logs out.
const AUTH_STORAGE = "car-advisor.auth";
/** fired when the server no longer accepts the token (expired, server key changed) */
export const LOGOUT_EVENT = "car-advisor:logout";

export function getAuth(): Auth | null {
  try {
    const raw = sessionStorage.getItem(AUTH_STORAGE);
    if (!raw) return null;
    const auth = JSON.parse(raw) as Auth;
    if (!auth.token || !auth.session_id) return null;
    if (auth.expires_at && auth.expires_at * 1000 < Date.now()) {
      sessionStorage.removeItem(AUTH_STORAGE);
      return null;
    }
    return auth;
  } catch {
    return null;
  }
}

export function setAuth(auth: Auth | null) {
  try {
    if (auth) sessionStorage.setItem(AUTH_STORAGE, JSON.stringify(auth));
    else sessionStorage.removeItem(AUTH_STORAGE);
  } catch {
    /* storage unavailable - the login lasts until the page is reloaded */
  }
}

const authHeaders = (): Record<string, string> => {
  const auth = getAuth();
  return auth ? { Authorization: `Bearer ${auth.token}` } : {};
};

/** 401 on these paths is about another credential (rep key) or a failed login attempt */
const NOT_EMPLOYEE_AUTH = /^\/api\/(rep|llm|auth)\//;

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
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      0,
      import.meta.env.DEV
        ? `השרת אינו זמין (${API_BASE}). האם ה-backend רץ?`
        : "השרת אינו זמין כרגע. בדקו את החיבור לאינטרנט ונסו שוב בעוד רגע.",
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401 && !NOT_EMPLOYEE_AUTH.test(path) && getAuth()) {
      // the token is no longer valid: back to the login screen
      setAuth(null);
      window.dispatchEvent(new Event(LOGOUT_EVENT));
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function readBytes(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader(); // older engines (and jsdom) lack Blob.arrayBuffer
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

const post = <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
  });

export const api = {
  catalog: () => request<Catalog>("/api/catalog"),
  /** employee number + code: back to the most recent conversation (or a new one);
   *  guest: a NEW conversation. Either way a token bound to that conversation */
  login: (body: LoginRequest) => post<LoginResponse>("/api/auth/login", body),
  /** my conversations, most recent first (employees only) */
  conversations: () => request<ConversationSummary[]>("/api/conversations"),
  /** a new conversation; the earlier ones are kept. A token bound to the new one */
  newConversation: () => post<LoginResponse>("/api/conversations"),
  /** back to one of my earlier conversations: a token bound to it */
  openConversation: (id: string) => post<LoginResponse>(`/api/conversations/${id}/open`),
  /** a message in the logged-in conversation (the token says which one) */
  chat: (message: string, sessionId: string) =>
    post<ChatResponse>("/api/chat", { session_id: sessionId, message }),
  session: (id: string) => request<SessionResponse>(`/api/session/${id}`),
  options: (id: string) => request<Options>(`/api/session/${id}/options`),
  ranking: (id: string) => request<Ranking>(`/api/session/${id}/ranking`),
  setAlternative: (id: string, choice: { car_id: string } | { waiver: true }) =>
    post<ChatResponse>(`/api/session/${id}/alternative`, choice),
  /** the PDF itself is the body; the server reads it in memory and keeps nothing */
  /** imageConsent: the employee agreed to send the page image to the model (unreadable PDF) */
  extractPayslip: async (file: Blob, imageConsent = false) =>
    request<PayslipExtraction>(`/api/payslip/extract${imageConsent ? "?vision=1" : ""}`, {
      method: "POST",
      body: await readBytes(file), // raw bytes: portable across fetch implementations
      headers: { "Content-Type": "application/pdf" },
    }),
  applyPayslip: (sessionId: string, values: PayslipValues) =>
    post<ChatResponse>("/api/payslip/apply", { session_id: sessionId, values }),
  tickets: () => request<Ticket[]>("/api/rep/tickets", { headers: repHeaders() }),
  answer: (id: string, answer_text: string, participation?: number) =>
    post<RepAnswerResponse>(`/api/rep/tickets/${id}/answer`, { answer_text, participation }, repHeaders()),
};
