// Единственное место, которое знает адреса бэкенда.
//
// Экраны обращаются только сюда: иначе адреса расползутся по семи файлам,
// и смена префикса превратится в поиск по всему проекту.
//
// ДВА РЕЖИМА РАБОТЫ.
//
// 1. Консоль и бэкенд на одном адресе (разработка через прокси Vite,
//    либо статика, отданная тем же сервером). Тогда `/api` — правильный
//    относительный путь, и настраивать нечего.
//
// 2. Консоль выложена отдельно (GitHub Pages), а бэкенд остаётся на
//    машине разработчика за туннелем. Тогда относительный путь ведёт в
//    никуда — на сам GitHub — и нужен полный адрес. Он задаётся при
//    сборке: `VITE_API_BASE=https://xxx.ngrok-free.app npm run build`.
//
// Адрес не хранится в коде намеренно: туннель меняет его при каждом
// перезапуске, и зашитый URL — гарантированно мёртвая ссылка через день.
//
// ТРИ ИСТОЧНИКА, В ПОРЯДКЕ СТАРШИНСТВА:
//
// 1. `?api=https://...` в адресной строке — и сразу запоминается. Это
//    главный способ для выложенной консоли: бесплатный туннель выдаёт
//    новый адрес при каждом запуске, и пересобирать фронт ради одной
//    строки — гарантированный способ однажды показать банку мёртвую
//    ссылку. Открыл ссылку с параметром — консоль работает.
// 2. Запомненный ранее адрес из localStorage.
// 3. Значение, вшитое при сборке (`VITE_API_BASE`), — для случая, когда
//    адрес постоянный.
//
// Пусто — значит бэкенд на том же адресе, что и консоль, и работает
// относительный `/api`, как в разработке через прокси Vite.
function resolveOrigin(): string {
  const KEY = "soro_api";
  try {
    const asked = new URLSearchParams(window.location.search).get("api");
    if (asked !== null) {
      // Пустой `?api=` — способ сбросить запомненный адрес и вернуться к
      // относительному пути, не лазая в инструменты разработчика.
      if (asked === "") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, asked);
      return asked.replace(/\/$/, "");
    }
    const saved = localStorage.getItem(KEY);
    if (saved) return saved.replace(/\/$/, "");
  } catch {
    // localStorage может быть недоступен (приватный режим, строгие
    // настройки) — это не повод падать, просто берём значение сборки.
  }
  return (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
}

const ORIGIN = resolveOrigin();

export const API_BASE = `${ORIGIN}/api`;

/** Куда сейчас ходит консоль. Пусто — на свой же адрес. */
export function apiOrigin(): string {
  return ORIGIN;
}

/** Задать адрес бэкенда вручную и перезагрузиться.
 *
 *  Нужно, когда консоль открыли без `?api=`: без адреса она стучится на
 *  свой же хост, где API нет, и все экраны молча висят в состоянии
 *  загрузки. Человек на встрече решает, что продукт сломан. */
export function setApiOrigin(value: string): void {
  const clean = value.trim().replace(/\/$/, "");
  if (clean) localStorage.setItem("soro_api", clean);
  else localStorage.removeItem("soro_api");
  window.location.reload();
}

/** Адрес WebSocket инбокса. Выводится из того же ORIGIN: http → ws. */
export function socketOrigin(): string {
  if (!ORIGIN) return `ws://${location.host}`;
  return ORIGIN.replace(/^http/, "ws");
}

// --- выбранный банк --------------------------------------------------------
//
// Консоль обслуживает несколько банков, и бэкенду надо сказать, чей экран
// открыт. Slug уходит заголовком `X-Workspace` со ВСЕХ запросов — так его
// не нужно протаскивать в каждую функцию этого файла и не забыть ни в
// одной. Выбор переживает перезагрузку: оператор возвращается туда, где
// работал.

const WORKSPACE_KEY = "soro_ws";

export function currentWorkspace(): string | null {
  return localStorage.getItem(WORKSPACE_KEY);
}

export function selectWorkspace(slug: string | null): void {
  if (slug) localStorage.setItem(WORKSPACE_KEY, slug);
  else localStorage.removeItem(WORKSPACE_KEY);
}

function workspaceHeader(): Record<string, string> {
  const slug = currentWorkspace();
  return slug ? { "X-Workspace": slug } : {};
}

/** Заголовки, которые нужны каждому запросу к API.
 *
 *  `ngrok-skip-browser-warning` — обход страницы-предупреждения бесплатного
 *  туннеля. Без него ngrok отдаёт браузеру HTML-заглушку вместо ответа,
 *  причём БЕЗ CORS-заголовков, и выложенная отдельно консоль получает
 *  «Failed to fetch» на каждом запросе. Значение неважно — важно само
 *  наличие заголовка.
 *
 *  На своём домене заголовок безвреден: сервер его игнорирует. */
function apiHeaders(): Record<string, string> {
  return { "ngrok-skip-browser-warning": "1", ...workspaceHeader() };
}

export interface WorkspaceRow {
  slug: string;
  name: string;
  documents: number;
  conversations: number;
  default: boolean;
}

export function listWorkspaces(): Promise<WorkspaceRow[]> {
  return request<WorkspaceRow[]>("/workspaces");
}

export function addWorkspace(slug: string, name: string): Promise<WorkspaceRow> {
  return request<WorkspaceRow>("/workspaces", {
    method: "POST",
    body: JSON.stringify({ slug, name }),
  });
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    // credentials НЕ включаем: консоль не использует куки, банк
    // определяется заголовком X-Workspace. При обращении с другого
    // домена (выложенная консоль → бэкенд за туннелем) режим "include"
    // требует от сервера Access-Control-Allow-Credentials: true, а
    // выдавать его вместе с конкретным origin — лишнее послабление ради
    // кук, которых нет. Браузер иначе блокирует все запросы на preflight.
    credentials: "same-origin",
    ...init,
    // Заголовки идут ПОСЛЕ ...init намеренно: иначе вызов со своими
    // заголовками (загрузка файла) затирал бы X-Workspace, и документ
    // уехал бы в чужой банк.
    headers: {
      "Content-Type": "application/json",
      ...apiHeaders(),
      ...(init?.headers as Record<string, string> | undefined),
    },
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
    // credentials НЕ включаем: консоль не использует куки, банк
    // определяется заголовком X-Workspace. При обращении с другого
    // домена (выложенная консоль → бэкенд за туннелем) режим "include"
    // требует от сервера Access-Control-Allow-Credentials: true, а
    // выдавать его вместе с конкретным origin — лишнее послабление ради
    // кук, которых нет. Браузер иначе блокирует все запросы на preflight.
    credentials: "same-origin",
    headers: apiHeaders(),
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

// --- каналы (экран 05) -----------------------------------------------------

export interface ChannelCard {
  id: "telegram" | "widget" | "whatsapp";
  title: string;
  // live — работает, wait — настроено наполовину, off — не подключён,
  // unknown — не смогли спросить (Telegram не ответил).
  state: "live" | "wait" | "off" | "unknown";
  note: string;
  conversations: number;
  bot?: string;
  link?: string | null;
  webhook?: { url: string; pending: number; error: string | null } | null;
  snippet?: string;
  // site_url — страница-пример сайта банка с виджетом (её показывают),
  // demo_url — технический полигон с враждебными стилями.
  site_url?: string | null;
  demo_url?: string | null;
}

export interface ChannelsInfo {
  days: number;
  public_base_url: string | null;
  channels: ChannelCard[];
}

export function getChannels(): Promise<ChannelsInfo> {
  return request<ChannelsInfo>("/channels");
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

export function inboxCounters(): Promise<{ waiting: number; active: number }> {
  return request<{ waiting: number; active: number }>("/inbox/counters");
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

// «Закрыть диалог» ≠ «вернуть боту». Второе продолжает разговор без
// оператора, первое его заканчивает: клиент получает прощание с просьбой
// оценить работу специалиста, а следующее его сообщение начнёт НОВЫЙ
// диалог.
export function closeConversation(
  id: number,
): Promise<{ status: string; rate_for: number | null }> {
  return request<{ status: string; rate_for: number | null }>(
    `/conversations/${id}/close`,
    { method: "POST" },
  );
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
  // Сокет обязан идти туда же, куда и остальное API: при отдельно
  // выложенной консоли `location.host` — это GitHub, где никакого
  // WebSocket нет, и инбокс молча перестаёт обновляться.
  if (!ORIGIN) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return new WebSocket(`${protocol}//${window.location.host}/ws/inbox`);
  }
  return new WebSocket(`${socketOrigin()}/ws/inbox`);
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
  // Средняя оценка по пятибалльной шкале; null, пока не поставили ни одной.
  rating: { total: number; average: number | null };
}

export function getAnalytics(days = 7): Promise<Analytics> {
  return request<Analytics>(`/analytics?days=${days}`);
}

// --- отчёт словами (экран 08) ----------------------------------------------
//
// Руководитель пишет фразой («нужен отчёт за эту неделю», «а по июню?»),
// бэкенд разбирает период сам и возвращает всё, из чего собран ответ:
// цифры, сводку, которую видела модель, и её текст. Панель справа на экране
// показывает `facts` дословно — это и есть доказательство, что модель
// пересказывает, а не считает.

export interface ReportPeriod {
  name: string;
  // «эта неделя · 11–17 августа 2026» — название и точные даты
  title: string;
  since: string;
  until: string;
  days: number;
  // период в вопросе не назвали, бэкенд взял неделю по умолчанию
  assumed: boolean;
}

export interface ReportAnswer {
  question: string;
  period: ReportPeriod;
  text: string;
  facts: string;
  // те же цифры, что на экране 07, только за период отчёта
  data: Omit<Analytics, "days">;
  warnings: string[];
  // модель не ответила: в `text` лежит сводка цифрами
  degraded: boolean;
  latency_ms: number;
}

export function askReport(question: string): Promise<ReportAnswer> {
  return request<ReportAnswer>("/reports/ask", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
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
    // credentials НЕ включаем: консоль не использует куки, банк
    // определяется заголовком X-Workspace. При обращении с другого
    // домена (выложенная консоль → бэкенд за туннелем) режим "include"
    // требует от сервера Access-Control-Allow-Credentials: true, а
    // выдавать его вместе с конкретным origin — лишнее послабление ради
    // кук, которых нет. Браузер иначе блокирует все запросы на preflight.
    credentials: "same-origin",
    headers: apiHeaders(),
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
}

// Все страницы одного сайта разом. Обход даёт по строке documents на
// страницу, у Эсхаты их полторы сотни — по одной удалять их из браузера
// значит полторы сотни запросов.
export async function deleteSite(host: string): Promise<{ deleted: number }> {
  const response = await fetch(
    `${API_BASE}/documents?host=${encodeURIComponent(host)}`,
    { method: "DELETE", credentials: "same-origin", headers: apiHeaders() },
  );
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return (await response.json()) as { deleted: number };
}
