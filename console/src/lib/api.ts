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
