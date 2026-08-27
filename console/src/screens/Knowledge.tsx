import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Doc,
  DocStatus,
  addSite,
  deleteDocument,
  deleteSite,
  listDocuments,
  uploadFile,
} from "../lib/api";
import Confirm, { ConfirmRequest } from "../components/Confirm";
import { Empty, Failed, Loading } from "../components/State";

// Экран 02 — База знаний. Разметка и классы взяты из прототипа
// (soro-business-console-2.html, секция kb): та же таблица, те же
// .fname / .ficon / .pill / .bar. Отличие одно — данные настоящие.
//
// Пока есть незавершённые документы, список опрашивается раз в 2 секунды:
// индексация идёт в фоновой очереди (правило 6.1 — внутри HTTP-запроса она
// не выполняется никогда), и узнать о её окончании иначе нельзя. Поллинг
// прекращается, когда всё дошло до ready или failed: вкладка, забытая на
// ночь, не должна дёргать бэкенд до утра.

const POLL_MS = 2000;
const IN_PROGRESS: DocStatus[] = ["queued", "indexing"];

const STATUS_LABEL: Record<DocStatus, string> = {
  queued: "В очереди",
  indexing: "Индексируется",
  ready: "Проиндексирован",
  failed: "Ошибка",
};

// классы пилюль — из прототипа: live (зелёная), wait (латунь), off (серая)
const STATUS_PILL: Record<DocStatus, string> = {
  queued: "off",
  indexing: "wait",
  ready: "live",
  failed: "off",
};

const KIND_LABEL: Record<string, string> = {
  pdf: "PDF",
  docx: "DOCX",
  xlsx: "XLSX",
  web: "WEB",
};

function volume(doc: Doc): string {
  if (doc.pages == null) return "—";
  return doc.kind === "xlsx" ? `${doc.pages} лист` : `${doc.pages} стр.`;
}

// «1 страница / 2 страницы / 5 страниц» — иначе в подтверждении удаления
// выходит «Это 146 страница».
function pageWord(n: number): string {
  const tail = n % 100;
  if (tail >= 11 && tail <= 14) return "страниц";
  switch (n % 10) {
    case 1:
      return "страница";
    case 2:
    case 3:
    case 4:
      return "страницы";
    default:
      return "страниц";
  }
}

// Корзина. В эталоне иконок-контуров нет вовсе (там только залитые
// прямоугольники логотипа и полилинии графиков), поэтому рисуем свою:
// stroke="currentColor" — цвет наследуется от кнопки и сам меняется на
// наведении, отдельного правила для svg не нужно.
function TrashIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M6 6l1 14h10l1-14" />
      <path d="M10 11v5M14 11v5" />
    </svg>
  );
}

function DeleteButton({
  onClick,
  disabled,
  what,
}: {
  onClick: () => void;
  disabled: boolean;
  what: string;
}) {
  // aria-label обязателен: текста в кнопке больше нет, и без него
  // скринридер прочитает пустую кнопку
  return (
    <button
      className="iconbtn"
      onClick={onClick}
      disabled={disabled}
      aria-label={`Удалить ${what}`}
      title={`Удалить ${what}`}
    >
      <TrashIcon />
    </button>
  );
}

// --- группировка страниц обхода -------------------------------------------
// Обход заводит по строке `documents` на каждую страницу сайта — у Эсхаты
// их полторы сотни, и таблица превращалась в простыню. Эталон рисует сайт
// ОДНОЙ строкой («eskhata.tj · 96 страниц»), к ней добавлен только
// раскрывающий шеврон. Группируем на фронте: связи между страницами в базе
// нет, но хост есть в source_url, и этого достаточно.

interface SiteGroup {
  host: string;
  pages: Doc[];
  chunks: number;
  status: DocStatus;
  chunksDone: number;
  chunksTotal: number;
}

// Внутри раскрытого сайта хост у всех строк один и тот же — показываем
// путь, иначе полтораста строк начинаются одинаково и читать их нельзя.
function pathOf(doc: Doc): string {
  if (!doc.source_url) return "—";
  try {
    const { pathname, search } = new URL(doc.source_url);
    return pathname + search;
  } catch {
    return doc.source_url;
  }
}

function hostOf(doc: Doc): string | null {
  if (doc.kind !== "web" || !doc.source_url) return null;
  try {
    return new URL(doc.source_url).host;
  } catch {
    return null;
  }
}

function groupStatus(pages: Doc[]): DocStatus {
  // сводный статус: пока хоть одна страница в работе — сайт «индексируется»,
  // упавшие важнее готовых, иначе ошибка потеряется внутри свёрнутой группы
  if (pages.some((p) => IN_PROGRESS.includes(p.status))) return "indexing";
  if (pages.some((p) => p.status === "failed")) return "failed";
  return "ready";
}

function split(docs: Doc[]): { files: Doc[]; sites: SiteGroup[] } {
  const files: Doc[] = [];
  const byHost = new Map<string, Doc[]>();

  for (const doc of docs) {
    const host = hostOf(doc);
    if (host === null) {
      files.push(doc);
      continue;
    }
    const bucket = byHost.get(host);
    if (bucket) bucket.push(doc);
    else byHost.set(host, [doc]);
  }

  const sites = [...byHost.entries()].map(([host, pages]) => ({
    host,
    pages,
    chunks: pages.reduce((sum, p) => sum + p.chunks, 0),
    status: groupStatus(pages),
    chunksDone: pages.reduce((sum, p) => sum + p.chunks_done, 0),
    chunksTotal: pages.reduce((sum, p) => sum + p.chunks_total, 0),
  }));

  return { files, sites };
}

function Status({ doc }: { doc: Doc }) {
  const percent =
    doc.chunks_total > 0 ? Math.round((doc.chunks_done / doc.chunks_total) * 100) : 0;

  return (
    <>
      <span className={`pill ${STATUS_PILL[doc.status]}`}>
        <span className="dot" />
        {STATUS_LABEL[doc.status]}
      </span>
      {IN_PROGRESS.includes(doc.status) && (
        <div className="bar" style={{ marginTop: 6 }}>
          <i style={{ width: `${percent}%` }} />
        </div>
      )}
      {doc.status === "failed" && doc.error && (
        <div className="fail" style={{ marginTop: 6 }}>
          {doc.error}
        </div>
      )}
    </>
  );
}

export default function Knowledge() {
  // `null` — «ещё не пришло», `[]` — «база знаний пуста». Разница видна
  // оператору: раньше он встречал «Пока пусто. Загрузите тарифы банка» при
  // полной базе, просто пока летел запрос.
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // раскрытые сайты, по умолчанию все свёрнуты
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Поиск по базе. На демо-стенде документов сотня, у банка их будут
  // тысячи, и без строки поиска экран превращается в бесконечную таблицу,
  // по которой невозможно проверить «а загружены ли тарифы».
  const [query, setQuery] = useState("");

  const found = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle || docs === null) return docs ?? [];
    return docs.filter(
      (doc) =>
        doc.title.toLowerCase().includes(needle) ||
        (doc.source_url ?? "").toLowerCase().includes(needle),
    );
  }, [docs, query]);

  const { files, sites } = useMemo(() => split(found), [found]);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
      setError("");
    } catch {
      setError("Бэкенд не отвечает");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!docs?.some((d) => IN_PROGRESS.includes(d.status))) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [docs, refresh]);

  async function onFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await uploadFile(file);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  // Удаление шло без try/catch: если документ уже удалён (или строка
  // устарела — список перерисовывается раз в 2 секунды), запрос падал
  // 404, ошибка уходила необработанной в консоль, refresh не вызывался,
  // и строка оставалась на экране. Человек жал «удалить» ещё раз, и так
  // по кругу. Теперь ошибка видна, а список обновляется в любом случае.
  // Подтверждение: удаление необратимо и уносит с собой все фрагменты,
  // а промахнуться легко — список перерисовывается раз в 2 секунды, и
  // строки под курсором успевают съехать.
  function askDelete(doc: Doc) {
    setConfirm({
      title: "Удалить документ?",
      text: `«${doc.title}» и все его фрагменты будут удалены безвозвратно. Бот перестанет отвечать по этому источнику.`,
      onOk: () => void onDelete(doc),
    });
  }

  function askDeleteSite(site: SiteGroup) {
    const count = site.pages.length;
    setConfirm({
      title: `Удалить ${site.host}?`,
      text: `${count} ${pageWord(count)} обхода и все их фрагменты будут удалены безвозвратно. Сайт можно будет обойти заново.`,
      okLabel: "Удалить сайт",
      onOk: () => void onDeleteSite(site),
    });
  }

  async function onDelete(doc: Doc) {
    setBusy(true);
    try {
      await deleteDocument(doc.id);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error
          ? `Не удалось удалить «${doc.title}»: ${err.message}`
          : "Не удалось удалить документ",
      );
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  async function onDeleteSite(site: SiteGroup) {
    setBusy(true);
    try {
      await deleteSite(site.host);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error
          ? `Не удалось удалить ${site.host}: ${err.message}`
          : "Не удалось удалить сайт",
      );
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  function toggle(host: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(host)) next.delete(host);
      else next.add(host);
      return next;
    });
  }

  async function onSite(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    try {
      await addSite(url.trim());
      setUrl("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось добавить сайт");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>
            База <em>знаний</em>
          </h1>
          <p>
            Загрузите тарифы, регламенты и условия — Soro отвечает только по ним.
            Обновили документ — ответы меняются в тот же час, без переобучения
            модели.
          </p>
        </div>
        <div className="spacer" />
        {/* Прозрачное поле лежит ПОВЕРХ кнопки: клик попадает физически в
            input, и диалог открывает сам браузер. Ни programmatic click,
            ни <label for> не годятся — в Chrome они молча не срабатывают,
            хотя в Edge открывают. Подробности в theme.css, .upload */}
        <span className="upload">
          <input
            ref={fileInput}
            className="file"
            type="file"
            accept=".pdf,.docx,.xlsx"
            onChange={onFile}
            disabled={busy}
            aria-label="Загрузить документы"
          />
          <span className="btn primary" aria-hidden="true">
            Загрузить документы
          </span>
        </span>
      </div>

      {/* Ошибка загрузки должна быть видна рядом с кнопкой, а не только
          в карточке ниже: иначе отказ выглядит как «ничего не произошло». */}
      {error && <Failed text={error} onRetry={refresh} />}

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="eyebrow">Добавить источник</div>
        <div
          style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
        >
          <form onSubmit={onSite} style={{ display: "flex", gap: 8, flex: 1 }}>
            <input
              className="text"
              placeholder="https://сайт-банка.tj/ — обойдём и проиндексируем"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
              style={{ flex: 1, minWidth: 240 }}
            />
            <button className="btn" type="submit" disabled={busy}>
              Обойти сайт
            </button>
          </form>
        </div>
      </div>

      {/* Строка поиска стоит НАД таблицей и всегда видна: искать документ
          прокруткой в тысяче строк невозможно, а именно этот вопрос —
          «а такой-то регламент у вас загружен?» — задают на встрече. */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input
            className="text"
            style={{ flex: 1, minWidth: 240 }}
            placeholder="Поиск по названию документа или адресу страницы"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Поиск по базе знаний"
          />
          {query && (
            <button className="btn" onClick={() => setQuery("")}>
              Сбросить
            </button>
          )}
          <span className="mono" style={{ fontSize: 11, color: "var(--muted2)" }}>
            {docs === null
              ? "…"
              : query
                ? `найдено ${found.length} из ${docs.length}`
                : `${docs.length} источников`}
          </span>
        </div>
      </div>

      <div className="card" style={{ padding: "6px 4px" }}>
        <table>
          <thead>
            <tr>
              <th>Источник</th>
              <th>Объём</th>
              <th>Фрагментов</th>
              <th>Статус</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {docs === null && !error && (
              <tr>
                <td colSpan={5}>
                  <Loading text="Читаю базу знаний…" />
                </td>
              </tr>
            )}
            {docs !== null && docs.length === 0 && (
              <tr>
                <td colSpan={5}>
                  <Empty
                    title="База знаний пуста"
                    hint={
                      "Загрузите тарифы банка (PDF, DOCX, XLSX) или дайте ссылку " +
                      "на сайт — обход соберёт страницы сам. Пока здесь пусто, " +
                      "бот на любой вопрос отвечает эскалацией."
                    }
                  />
                </td>
              </tr>
            )}
            {files.map((doc) => (
              <tr key={doc.id}>
                <td>
                  <div className="fname">
                    <div className="ficon">{KIND_LABEL[doc.kind] ?? doc.kind}</div>
                    <div>
                      {doc.title}
                      <br />
                      <span
                        className="mono"
                        style={{ fontSize: "10.5px", color: "var(--muted2)" }}
                      >
                        {doc.source_url ?? `фрагментов: ${doc.chunks}`}
                      </span>
                    </div>
                  </div>
                </td>
                <td className="mono">{volume(doc)}</td>
                <td className="mono">{doc.chunks || "—"}</td>
                <td>
                  <Status doc={doc} />
                </td>
                <td>
                  <DeleteButton
                    onClick={() => askDelete(doc)}
                    disabled={busy}
                    what={`«${doc.title}»`}
                  />
                </td>
              </tr>
            ))}

            {/* Сайт — одной строкой, как в эталоне. Шеврон разворачивает
                список его страниц отдельными строками ниже. */}
            {sites.map((site) => {
              const open = expanded.has(site.host);
              const percent =
                site.chunksTotal > 0
                  ? Math.round((site.chunksDone / site.chunksTotal) * 100)
                  : 0;
              return (
                <Fragment key={site.host}>
                  <tr>
                    <td>
                      <div className="fname">
                        <button
                          className={`chev${open ? " open" : ""}`}
                          onClick={() => toggle(site.host)}
                          aria-expanded={open}
                          aria-label={
                            open
                              ? `Свернуть страницы ${site.host}`
                              : `Показать страницы ${site.host}`
                          }
                          title={open ? "Свернуть" : "Показать страницы"}
                        />
                        <div className="ficon">WEB</div>
                        <div>
                          {site.host}
                          <br />
                          <span
                            className="mono"
                            style={{ fontSize: "10.5px", color: "var(--muted2)" }}
                          >
                            {site.pages.length} {pageWord(site.pages.length)} ·{" "}
                            {site.chunks} фрагментов
                          </span>
                        </div>
                      </div>
                    </td>
                    <td className="mono">
                      {site.pages.length} {pageWord(site.pages.length)}
                    </td>
                    <td className="mono">{site.chunks || "—"}</td>
                    <td>
                      <span className={`pill ${STATUS_PILL[site.status]}`}>
                        <span className="dot" />
                        {STATUS_LABEL[site.status]}
                      </span>
                      {IN_PROGRESS.includes(site.status) && (
                        <div className="bar" style={{ marginTop: 6 }}>
                          <i style={{ width: `${percent}%` }} />
                        </div>
                      )}
                    </td>
                    <td>
                      <DeleteButton
                        onClick={() => askDeleteSite(site)}
                        disabled={busy}
                        what={`сайт ${site.host} целиком`}
                      />
                    </td>
                  </tr>

                  {open &&
                    site.pages.map((doc) => (
                      <tr key={doc.id} className="sub">
                        <td>
                          <span
                            className="mono"
                            style={{ fontSize: "11px", color: "var(--muted)" }}
                          >
                            {pathOf(doc)}
                          </span>
                          <br />
                          <span
                            style={{ fontSize: "11px", color: "var(--muted2)" }}
                          >
                            {doc.title}
                          </span>
                        </td>
                        <td className="mono">{volume(doc)}</td>
                        <td className="mono">{doc.chunks || "—"}</td>
                        <td>
                          <Status doc={doc} />
                        </td>
                        <td>
                          <DeleteButton
                            onClick={() => askDelete(doc)}
                            disabled={busy}
                            what={`страницу ${pathOf(doc)}`}
                          />
                        </td>
                      </tr>
                    ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3 className="sec">Как это работает</h3>
      <div className="grid g3">
        <div className="card">
          <div className="eyebrow">Шаг 1 · Разбор</div>
          <p style={{ margin: 0, fontSize: "12.5px", color: "var(--muted)" }}>
            PDF, Word, Excel и страницы сайта разбираются на смысловые фрагменты.
            Сканы проходят распознавание — таджикская кириллица поддерживается.
          </p>
        </div>
        <div className="card">
          <div className="eyebrow">Шаг 2 · Поиск</div>
          <p style={{ margin: 0, fontSize: "12.5px", color: "var(--muted)" }}>
            Гибридный поиск: по смыслу и по точным формулировкам одновременно.
            Работает на смеси таджикского и русского в одном вопросе.
          </p>
        </div>
        <div className="card">
          <div className="eyebrow">Шаг 3 · Ответ</div>
          <p style={{ margin: 0, fontSize: "12.5px", color: "var(--muted)" }}>
            Soro формулирует ответ строго по найденным фрагментам и подставляет
            ссылку на документ и страницу.
          </p>
        </div>
      </div>

      <Confirm request={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}
