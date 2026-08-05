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
