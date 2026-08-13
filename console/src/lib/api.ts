// Единственное место, которое знает адреса бэкенда.
//
// Экраны обращаются только сюда: иначе адреса расползутся по семи файлам,
// и смена префикса превратится в поиск по всему проекту.
export const API_BASE = "/api";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as T;
}

// Входа в консоль здесь нет намеренно: демо-стенд открывается сразу на
// экране 01. Подробности — в комментарии к App.tsx.

// Дальше по мере готовности бэкенда: documents, playground, inbox,
// analytics, подписка на /ws/inbox.

// --- база знаний (экран 02) ---

export type DocKind = "pdf" | "docx" | "xlsx" | "web";
export type DocStatus = "queued" | "indexing" | "ready" | "failed";

export interface Doc {
  id: number;
  kind: DocKind;
  title: string;
  status: DocStatus;
  source_url: string | null;
  pages: number | null;
  chunks: number;
  chunks_done: number;
  chunks_total: number;
  error: string | null;
}

export function listDocuments(): Promise<Doc[]> {
  return request<Doc[]>("/documents");
}

export async function uploadFile(file: File): Promise<Doc> {
  const form = new FormData();
  form.append("file", file);
  // Content-Type не ставим руками: браузер сам добавит boundary для multipart
  const response = await fetch(`${API_BASE}/documents`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return (await response.json()) as Doc;
}

export function addSite(url: string): Promise<Doc> {
  return request<Doc>("/documents", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

// --- воркспейс и контур безопасности (экран 01) ---------------------------

export interface Security {
  kb_only: boolean;
  cite_sources: boolean;
  audit_log: boolean;
  mask_pii: boolean;
}

export interface WorkspaceInfo {
  slug: string;
  name: string;
  // настоящее имя модели с сервера: в эталоне написано «Soro-27B · FP8»,
  // а отвечает GPTQ-int4, и подпись на демо должна совпадать с фактом
  model: string;
  security: Security;
  // адрес этого стенда: из него собирается сниппет виджета на экране 05.
  // На демо это ngrok, и он меняется при каждом перезапуске туннеля
  public_base_url: string;
  telegram_bot: string;
}

export function getWorkspace(): Promise<WorkspaceInfo> {
  return request<WorkspaceInfo>("/workspace");
}

// --- живой омниканальный диалог (экран 04) ---------------------------------
//
// Экран 04 по ТЗ презентационный, и сценарий на нём остаётся. Но рядом со
// сценарием он показывает диалог, который действительно случился: те же
// три устройства, только сообщения настоящие.

export interface OmniMessage {
  channel: string;
  role: "user" | "assistant" | "operator";
  text: string;
  created_at: string;
}

export interface OmniIdentity {
  channel: string;
  external_id: string;
}

export interface OmniLive {
  empty: boolean;
  conversation_id?: number;
  status?: string;
  contact?: { id: number | null; display_name: string | null };
  identities: OmniIdentity[];
  channels: string[];
  messages: OmniMessage[];
}

export function getOmniLatest(): Promise<OmniLive> {
  return request<OmniLive>("/omni/latest");
}

// --- обзор (экран 01) ------------------------------------------------------
//
// Ряды `spark` приходят по одному числу на день и уже содержат нули за
// дни без диалогов: линия обязана показывать провал провалом.

export interface ReadinessItem {
  title: string;
  done: boolean;
  hint: string;
}

export interface OverviewData {
  days: number;
  conversations: {
    total: number;
    by_bot: number;
    bot_share: number;
    channels: string[];
    spark: number[];
    spark_bot_share: number[];
  };
  latency: {
    median_ms: number | null;
    p95_ms: number | null;
    spark: number[];
  };
  citations: {
    answers: number;
    cited: number;
    share: number;
    spark: number[];
  };
  readiness: ReadinessItem[];
}

export function getOverview(): Promise<OverviewData> {
  return request<OverviewData>("/overview");
}

// --- инбокс оператора (экран 06) -------------------------------------------

export type InboxStatus = "waiting" | "active" | "resolved";

export interface InboxCard {
  conversation_id: number;
  escalation_id: number;
  reason: string;
  created_at: string;
  taken_by: string | null;
  resolved_at: string | null;
  display_name: string | null;
  channel: string | null;
  preview: string;
  last_at: string | null;
}

export interface InboxMessage {
  id: number;
  role: "user" | "assistant" | "operator" | "system";
  channel: string;
  text: string;
  created_at: string;
  latency_ms: number | null;
  chunks_used: number[];
}

export interface InboxHint {
  chunk_id: number;
  title: string;
  page: number | null;
  text: string;
}

export interface ConversationCard {
  conversation_id: number;
  status: string;
  display_name: string | null;
  channels: string[];
  escalation: {
    id: number;
    reason: string;
    taken_by: string | null;
    created_at: string;
  } | null;
  messages: InboxMessage[];
  hint: InboxHint[];
}

export function listInbox(status: InboxStatus): Promise<InboxCard[]> {
  return request<InboxCard[]>(`/inbox?status=${status}`);
}

export function getConversation(id: number): Promise<ConversationCard> {
  return request<ConversationCard>(`/conversations/${id}`);
}

export function takeConversation(id: number): Promise<{ taken_by: string }> {
  return request<{ taken_by: string }>(`/conversations/${id}/take`, {
    method: "POST",
    body: JSON.stringify({ operator: "operator" }),
  });
}

export function replyToClient(id: number, text: string): Promise<unknown> {
  return request(`/conversations/${id}/reply`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function returnToBot(id: number): Promise<{ status: string }> {
  return request<{ status: string }>(`/conversations/${id}/resolve`, {
    method: "POST",
  });
}

// Сокет живёт вне `request`: у него нет ни заголовков, ни JSON-ответа. Но
// адрес всё равно собирается здесь — правило «адреса бэкенда знает только
// этот файл» действует и для ws.
export function inboxSocket(): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new WebSocket(`${protocol}//${window.location.host}/ws/inbox`);
}

// --- аналитика (экран 07) --------------------------------------------------

export interface Analytics {
  days: number;
  conversations: {
    total: number;
    by_bot: number;
    by_operator: number;
    bot_share: number;
  };
  hours_saved: number;
  median_latency_ms: number | null;
  channels: { channel: string; conversations: number }[];
  languages: { lang: string; messages: number }[];
  top_questions: { question: string; count: number }[];
  attention: { no_answer: number };
  // Средней оценки нет: таблица feedback пуста и без эндпоинта.
  rating: number | null;
}

export function getAnalytics(days = 7): Promise<Analytics> {
  return request<Analytics>(`/analytics?days=${days}`);
}

export function setSecurity(
  changes: Partial<Omit<Security, "kb_only">>,
): Promise<{ security: Security }> {
  return request<{ security: Security }>("/workspace/security", {
    method: "PUT",
    body: JSON.stringify(changes),
  });
}

export async function deleteDocument(id: number): Promise<void> {
  const response = await fetch(`${API_BASE}/documents/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
}

// Все страницы одного сайта разом. Обход даёт по строке documents на
// страницу, у Эсхаты их полторы сотни — по одной удалять их из браузера
// значит полторы сотни запросов.
export async function deleteSite(host: string): Promise<{ deleted: number }> {
  const response = await fetch(
    `${API_BASE}/documents?host=${encodeURIComponent(host)}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return (await response.json()) as { deleted: number };
}
