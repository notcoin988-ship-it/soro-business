import { useCallback, useEffect, useRef, useState } from "react";
import {
  Doc,
  DocStatus,
  addSite,
  deleteDocument,
  listDocuments,
  uploadFile,
} from "../lib/api";

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
  const [docs, setDocs] = useState<Doc[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

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
    if (!docs.some((d) => IN_PROGRESS.includes(d.status))) return;
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
        <button
          className="btn primary"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          Загрузить документы
        </button>
      </div>

      {/* Поле файла скрыто (см. .file в theme.css): диалог открывает кнопка
          «Загрузить документы» в шапке — так нарисовано в эталоне, и так
          не видно нативной надписи браузера на языке системы. */}
      <input
        ref={fileInput}
        className="file"
        type="file"
        accept=".pdf,.docx,.xlsx"
        onChange={onFile}
      />

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
        {error && (
          <div className="fail" style={{ marginTop: 8 }}>
            {error}
          </div>
        )}
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
            {docs.length === 0 && (
              <tr>
                <td colSpan={5} style={{ color: "var(--muted2)", fontSize: "12.5px" }}>
                  Пока пусто. Загрузите тарифы банка или дайте ссылку на сайт.
                </td>
              </tr>
            )}
            {docs.map((doc) => (
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
                  <button
                    className="linkbtn"
                    disabled={busy}
                    onClick={() => onDelete(doc)}
                  >
                    удалить
                  </button>
                </td>
              </tr>
            ))}
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
    </>
  );
}
