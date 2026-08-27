import { Fragment, useEffect, useRef, useState } from "react";
import { API_BASE, currentWorkspace, getWorkspace } from "../lib/api";

// Экран 03 «Площадка · стеклянный ящик».
//
// Разметка и классы — из эталона (soro-business-console-2.html, секция pg):
// .pg / .chat / .chatbar / .chatlog / .asks слева, .glass / .glasshead /
// .stagechip / .glassbody / .telemetry справа. Отличие одно — данные
// настоящие: ответ стримится по SSE, фрагменты приходят от поиска.
//
// Порядок событий задан приложением А ТЗ: retrieval приходит ДО первого
// delta. Это не деталь реализации, а смысл экрана: зритель должен увидеть,
// на чём основан ответ, ДО того как прочитает сам ответ.

const STAGES = {
  idle: "ожидание",
  request: "запрос",
  retrieval: "поиск по базе",
  generation: "генерация",
  done: "готово",
  error: "ошибка",
} as const;

type Stage = keyof typeof STAGES;

interface Fragment {
  n: number;
  chunk_id: number;
  title: string;
  page: number | null;
  source_url: string | null;
  score: number;
  text: string;
}

interface Telemetry {
  search_ms: number;
  generation_ms: number;
  total_ms: number;
  tokens: number;
}

function fragmentSource(fragment: Fragment): string {
  return fragment.page !== null
    ? `${fragment.title}, стр. ${fragment.page}`
    : fragment.title;
}

/** Источники под ответом: чем именно он подкреплён.
 *
 *  ДВА УРОВНЯ ПОДРОБНОСТИ. Обычному зрителю нужны документ и страница —
 *  этого достаточно, чтобы проверить ответ. ИТ-службе и безопасности нужны
 *  оценки близости и тайминги, но вываливать их всем — значит превратить
 *  понятный ответ в отладочный вывод. Поэтому техника скрыта за строкой
 *  «показать технические детали». */
function Sources({
  fragments,
  telemetry,
}: {
  fragments: Fragment[];
  telemetry: Telemetry | null;
}) {
  const [tech, setTech] = useState(false);
  const best = fragments.reduce((top, fragment) => Math.max(top, fragment.score), 0);

  return (
    <div className="sources">
      <div className="slbl">
        Источники · {fragments.length}
      </div>

      {fragments.map((fragment) => (
        <div className="srcitem" key={fragment.chunk_id}>
          <span className="sicon">📄</span>
          <span>{fragment.title}</span>
          {fragment.page !== null && <span className="spage">с. {fragment.page}</span>}
          {tech && <span className="sscore">{formatScore(fragment.score)}</span>}
        </div>
      ))}

      <button className="techtog" onClick={() => setTech(!tech)}>
        {tech ? "Скрыть технические детали" : "Показать технические детали"}
      </button>

      {tech && (
        <div className="abox" style={{ marginTop: 9 }}>
          <div className="albl">Разбор ответа</div>
          <ul>
            <li>Лучшая близость: {formatScore(best)}</li>
            <li>Фрагментов процитировано: {fragments.length}</li>
            {telemetry && <li>Поиск: {telemetry.search_ms} мс</li>}
            {telemetry && <li>Генерация: {telemetry.generation_ms} мс</li>}
            {telemetry && <li>Токенов: {telemetry.tokens}</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

// «0,69» — как в эталоне: запятая, два знака
function formatScore(score: number): string {
  return score.toFixed(2).replace(".", ",");
}

// Ссылки [1] в ответе → надстрочные бейджи sup.cite, как на экране 06.
// Разбираем сами, а не через dangerouslySetInnerHTML: текст приходит от
// модели, и вставлять его как разметку — прямой путь к XSS.
function withCitations(
  text: string,
  onHover: (n: number | null) => void,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  // Форма скобки — та же, что разбирает бэкенд (`CITE_RE` в core/llm.py).
  // Модель регулярно объединяет ссылки: «[1, 2]» — две, «[2.1]» — одна,
  // подпункт второго фрагмента. Строгая форма `\[(\d+)\]` оставляла такую
  // скобку сырым текстом прямо в ответе клиенту.
  const re = /\[(\d{1,2}(?:\s*[.,;]\s*\d{1,2})*)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = re.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    // запятая разделяет ссылки, точка — нумерацию внутри фрагмента
    const numbers = match[1]
      .split(/[,;]/)
      .map((part) => Number(part.match(/\d{1,2}/)?.[0]))
      .filter((n, index, all) => !Number.isNaN(n) && all.indexOf(n) === index);

    numbers.forEach((number) => {
      parts.push(
        <sup
          key={`c${key++}`}
          className="cite"
          onMouseEnter={() => onHover(number)}
          onMouseLeave={() => onHover(null)}
        >
          {number}
        </sup>,
      );
    });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

// Завершённая пара «вопрос — ответ». Лог их копит: в эталоне каждый клик
// по заготовке очищал лог (`log.innerHTML=''`), но там прокручивалась
// заготовленная анимация на один сценарий. У нас это чат, и стирать
// предыдущий ответ при новом вопросе — значит терять то, что человек
// только что читал.
//
// Эта история — то, что видит человек. В модель предыдущие реплики
// уходят отдельно: их помнит бэкенд по `thread_id`, который вкладка
// присылает с каждым вопросом. Держать эти два списка в синхроне не
// нужно и не следует — здесь показываем разговор, там храним то, что
// реально ушло в промпт (маскированный текст, без ссылок [1]).
interface Exchange {
  question: string;
  answer: string;
  escalated: boolean;
  belowThreshold: boolean;
  error: string;
}

export default function Playground() {
  const [question, setQuestion] = useState("");
  // Имя модели с сервера. В эталоне зашито «Soro-27B · FP8», а отвечает
  // GPTQ-int4 — на демо перед ИТ-службой подпись обязана совпадать с тем,
  // что реально работает. Префикс вендора отбрасываем, он только мешает.
  const [model, setModel] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState("");
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState("");
  const [escalated, setEscalated] = useState(false);
  const [belowThreshold, setBelowThreshold] = useState(false);
  const [hot, setHot] = useState<number | null>(1);
  // развёрнутые фрагменты: по умолчанию текст свёрнут до шести строк,
  // иначе один чанк на 400 токенов занимает панель целиком
  const [opened, setOpened] = useState<Set<number>>(new Set());

  const logRef = useRef<HTMLDivElement>(null);
  // Отмена текущего потока: уход с экрана или новый вопрос обрывают
  // предыдущий запрос, иначе его дельты продолжат дописываться в ответ.
  const abortRef = useRef<AbortController | null>(null);
  // Ветка диалога: по ней бэкенд помнит предыдущие реплики. Одна на
  // вкладку и на всё время её жизни — перезагрузка страницы начинает
  // разговор заново, как и должно быть на тестовом экране.
  const threadRef = useRef(
    `pg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
  );
  const threadId = threadRef.current;

  const busy =
    stage === "request" || stage === "retrieval" || stage === "generation";

  // На какие фрагменты ответ реально сослался. Берём номера из ссылок [n]
  // в тексте, а не весь список найденного: в панели справа лежат все
  // кандидаты поиска, но под ответом должно стоять только то, чем он
  // подкреплён, — иначе «источники» превращаются в список похожих
  // документов и перестают что-либо доказывать.
  const cited = Array.from(
    new Set(
      Array.from(answer.matchAll(/\[(\d{1,2}(?:\s*[.,;]\s*\d{1,2})*)\]/g)).flatMap((match) =>
        match[1].split(/[,;]/).map((part) => parseInt(part, 10)),
      ),
    ),
  )
    .filter((number) => Number.isFinite(number))
    .map((number) => fragments.find((fragment) => fragment.n === number))
    .filter((fragment): fragment is Fragment => fragment !== undefined);

  // лог всегда прокручен вниз: ответ печатается вживую
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [answer, asked, history]);

  useEffect(() => {
    getWorkspace()
      .then((info) => setModel(info.model.split("/").pop() ?? info.model))
      .catch(() => setModel(""));
  }, []);

  // Уходим с экрана посреди генерации — соединение закрываем, иначе
  // бэкенд продолжит гнать поток в никуда.
  useEffect(() => () => abortRef.current?.abort(), []);

  async function ask(text: string) {
    if (!text.trim() || busy) return;

    abortRef.current?.abort();
    // предыдущая пара уезжает в историю, а не стирается
    if (asked) {
      setHistory((current) => [
        ...current,
        { question: asked, answer, escalated, belowThreshold, error },
      ]);
    }
    setAsked(text);
    setQuestion("");
    setAnswer("");
    setFragments([]);
    setTelemetry(null);
    setError("");
    setEscalated(false);
    setBelowThreshold(false);
    setHot(1);
    setOpened(new Set());
    setStage("request");

    // Воркспейс читаем один раз: он нужен и для POST, и для потока ниже.
    const workspace = currentWorkspace();
    let messageId: string;
    try {
      const response = await fetch(`${API_BASE}/playground/messages`, {
        method: "POST",
        // Воркспейс идёт и отсюда: этот fetch собран вручную, мимо
        // `request()` из lib/api, и без заголовка площадка спрашивала бы
        // банк по умолчанию, а не тот, что выбран в шапке.
        headers: {
          "Content-Type": "application/json",
          // Обход заглушки бесплатного туннеля — см. apiHeaders в lib/api.
          "ngrok-skip-browser-warning": "1",
          ...(workspace ? { "X-Workspace": workspace } : {}),
        },
        // credentials НЕ включаем: кук нет, а с другого домена режим
        // "include" требует Access-Control-Allow-Credentials и валит
        // запрос ещё на preflight. См. комментарий в lib/api.ts.
        credentials: "same-origin",
        body: JSON.stringify({ text, thread_id: threadId }),
      });
      if (!response.ok) throw new Error(await response.text());
      messageId = (await response.json()).message_id;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Бэкенд не отвечает");
      setStage("error");
      return;
    }

    // ПОТОК ЧИТАЕМ ЧЕРЕЗ fetch, А НЕ EventSource.
    //
    // EventSource проще, но не умеет отправлять заголовки вовсе — а они
    // нужны: бесплатный туннель без `ngrok-skip-browser-warning` отдаёт
    // браузеру HTML-заглушку вместо потока, и площадка на выложенной
    // консоли обрывалась на «Соединение прервано». Заголовок воркспейса
    // здесь тоже уходит по-человечески, а не параметром в адресе.
    //
    // Взамен приходится разбирать кадры SSE самим: они разделены пустой
    // строкой, внутри строки `event:` и `data:`.
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(
        `${API_BASE}/playground/stream?message_id=${encodeURIComponent(messageId)}`,
        {
          headers: {
            Accept: "text/event-stream",
            "ngrok-skip-browser-warning": "1",
            ...(workspace ? { "X-Workspace": workspace } : {}),
          },
          credentials: "same-origin",
          signal: controller.signal,
        },
      );
      if (!response.ok || response.body === null) {
        throw new Error(await response.text());
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Кадры отделены пустой строкой. Последний кусок может быть
        // неполным — оставляем его в буфере до следующего чтения.
        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          split = buffer.indexOf("\n\n");

          let name = "message";
          const payload: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) payload.push(line.slice(5).trim());
          }
          if (payload.length === 0) continue;
          const data = JSON.parse(payload.join("\n"));

          if (name === "retrieval") {
            setFragments(data.fragments);
            setBelowThreshold(!data.has_answer);
            setStage(data.has_answer ? "generation" : "retrieval");
          } else if (name === "delta") {
            setAnswer((current) => current + data.text);
          } else if (name === "final") {
            if (data.text) setAnswer(data.text);
            setTelemetry(data.telemetry);
            setEscalated(data.escalated);
            setStage("done");
          } else if (name === "error") {
            setError(data.detail ?? "Ошибка на стороне сервера");
            setStage("error");
          }
        }
      }
    } catch (err) {
      // Отмена — это уход с экрана или новый вопрос, а не поломка.
      if ((err as Error).name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Соединение прервано");
      setStage("error");
    }
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>
            Площадка · <em>стеклянный ящик</em>
          </h1>
          <p>
            Слева — что видит клиент. Справа — что происходит внутри: какие
            фрагменты нашлись, насколько подходят и сколько заняло. Эту панель
            показывайте ИТ-службе и безопасности.
          </p>
        </div>
      </div>

      <div className="pg">
        <div className="chat">
          <div className="chatbar">
            <span className="pill live">
              <span className="dot" />
              {model || "модель…"}
            </span>
            <span style={{ color: "var(--muted2)" }}>·</span>
            <span>База знаний: Demo Bank</span>
          </div>

          <div className="chatlog" ref={logRef}>
            {!asked && history.length === 0 && (
              <div className="empty">
                Задайте вопрос — на таджикском или русском.<br />
                Ответ печатается вживую, а справа виден разбор поиска.
              </div>
            )}

            {/* Прошлые пары. Ссылки в них не подсвечивают панель справа:
                фрагменты там уже от нового вопроса, и подсветка вела бы
                не туда. */}
            {history.map((item, index) => (
              <Fragment key={index}>
                <div className="msg u">{item.question}</div>
                <div className="msg a">
                  <div className="avatar">
                    <SoroMark />
                  </div>
                  <div
                    className={`abody${
                      item.escalated || item.belowThreshold ? " warn" : ""
                    }`}
                  >
                    {item.error ? (
                      <span className="fail">{item.error}</span>
                    ) : item.belowThreshold ? (
                      "Ин маълумот дар ҳуҷҷатҳои бонк нест — мутахассисро пайваст мекунам."
                    ) : (
                      withCitations(item.answer, () => {})
                    )}
                    {item.escalated && (
                      <div className="esc">⤴ Передан оператору</div>
                    )}
                  </div>
                </div>
              </Fragment>
            ))}

            {asked && <div className="msg u">{asked}</div>}

            {asked && stage === "request" && (
              <div className="think">
                <div className="avatar">
                  <SoroMark />
                </div>
                <div className="dots">
                  <i />
                  <i />
                  <i />
                </div>
              </div>
            )}

            {(answer || belowThreshold || error) && (
              <div className="msg a">
                <div className="avatar">
                  <SoroMark />
                </div>
                <div
                  className={`abody${escalated || belowThreshold ? " warn" : ""}`}
                >
                  {error ? (
                    <span className="fail">{error}</span>
                  ) : belowThreshold ? (
                    "Ин маълумот дар ҳуҷҷатҳои бонк нест — мутахассисро пайваст мекунам."
                  ) : (
                    withCitations(answer, setHot)
                  )}
                  {stage === "generation" && <span className="caret" />}
                  {escalated && stage === "done" && (
                    <div className="esc">⤴ Передан оператору</div>
                  )}

                  {/* Источники под самим ответом, а не только в панели
                      справа. Панель — для ИТ-службы, она разбирает поиск;
                      здесь же клиентский вид: на что опирался ответ.
                      Первый вопрос банка к любому ответу — «откуда это». */}
                  {stage === "done" && !error && !belowThreshold && cited.length > 0 && (
                    <Sources fragments={cited} telemetry={telemetry} />
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Поле вопроса — единственный вход. Кнопки-заготовки эталона
              убраны: площадка нужна банку, чтобы проверять СВОИ вопросы, а
              не четыре зашитых. */}
          <form
            className="asks"
            onSubmit={(event) => {
              event.preventDefault();
              ask(question);
            }}
          >
            <input
              className="text"
              style={{ flex: 1, minWidth: 200 }}
              placeholder="Свой вопрос — на таджикском или русском"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              disabled={busy}
            />
            <button
              className="btn"
              type="submit"
              disabled={busy || !question.trim()}
            >
              Спросить
            </button>
          </form>
        </div>

        <div className="glass">
          <div className="glasshead">
            <div>
              <b>Что нашлось в документах</b>
              <small>фрагменты, переданные модели</small>
            </div>
            <div className={`stagechip${busy ? " go" : ""}`}>{STAGES[stage]}</div>
          </div>

          <div className="glassbody">
            {fragments.length === 0 && !belowThreshold && (
              <div className="empty">Пока пусто.</div>
            )}
            {belowThreshold && fragments.length === 0 && (
              <div className="empty">
                Ни один фрагмент не прошёл
                <br />
                порог релевантности.
                <br />
                <br />
                Модели нечего процитировать —<br />
                диалог уходит оператору.
              </div>
            )}
            {fragments.map((fragment) => (
              <div
                key={fragment.chunk_id}
                className={
                  `frag${hot === fragment.n ? " hot" : ""}` +
                  (opened.has(fragment.chunk_id) ? " open" : "")
                }
              >
                <div className="fragtop">
                  <span className="fragsrc">{fragmentSource(fragment)}</span>
                  <div className="relbar">
                    <div className="t">
                      <i style={{ width: `${Math.round(fragment.score * 100)}%` }} />
                    </div>
                    <span>{formatScore(fragment.score)}</span>
                  </div>
                </div>
                <div
                  className="fragtxt"
                  title="Нажмите, чтобы развернуть фрагмент целиком"
                  onClick={() =>
                    setOpened((current) => {
                      const next = new Set(current);
                      if (next.has(fragment.chunk_id)) next.delete(fragment.chunk_id);
                      else next.add(fragment.chunk_id);
                      return next;
                    })
                  }
                >
                  {fragment.text}
                </div>
              </div>
            ))}
          </div>

          <div className="telemetry">
            <Metric name="поиск" value={telemetry && `${telemetry.search_ms} мс`} />
            <Metric
              name="генерация"
              value={telemetry && `${telemetry.generation_ms} мс`}
            />
            <Metric name="всего" value={telemetry && `${telemetry.total_ms} мс`} />
            <Metric name="токенов" value={telemetry && String(telemetry.tokens)} />
          </div>
        </div>
      </div>
    </>
  );
}

function Metric({ name, value }: { name: string; value: string | null }) {
  return (
    <div className="tm">
      <span>{name}</span>
      <b>{value ?? "—"}</b>
    </div>
  );
}

// Тот же знак, что в шапке консоли и в эталоне рядом с ответом бота.
function SoroMark() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16">
      <rect x="2" y="9" width="6" height="6" rx="1.6" fill="#E8506B" />
      <rect x="9" y="2" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
      <rect x="9" y="16" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
      <rect x="16" y="9" width="6" height="6" rx="1.6" fill="#DCA84C" />
    </svg>
  );
}
