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

// Вход в консоль: один общий логин-пароль на воркспейс (раздел 9).
// Ролей и регистрации нет — раздел 1.2 выносит это за скобки версии.
export async function login(password: string): Promise<void> {
  await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ login: "admin", password }),
  });
}

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
