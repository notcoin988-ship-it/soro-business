import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/api";

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

// Четыре заготовки, как в эталоне; последняя — заведомо без ответа.
//
// ВАЖНО про формулировки. В эталоне и в сценарии приёмки фигурирует вклад
// «Ояндасоз» — его у банка НЕ СУЩЕСТВУЕТ, прототип его выдумал для
// красивого макета (проверено: в базе знаний 0 фрагментов с этим словом).
// Спрашивать про него значит показывать на демо эскалацию вместо ответа.
// Поэтому берём настоящие продукты Эсхаты: «Фоиданок», тарифы, переводы.
// Вопросы совпадают с golden.yaml, то есть уже проверены прогоном.
const PRESETS = [
  {
    label: "Вопрос на таджикском",
    text: "Фоизи депозити онлайни «Фоиданок» чанд аст?",
  },
  {
    label: "Вопрос на русском",
    text: "Сколько стоит открытие счёта физическому лицу?",
  },
  {
    label: "Смешанный язык",
    text: "Салом! Подскажите, комиссия за перевод в другой банк чанд аст?",
  },
  {
    label: "Вопроса нет в базе",
    text: "Почему у меня списали 90 сомони вчера вечером?",
    danger: true,
  },
];

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
  const re = /\[(\d{1,2})\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = re.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const number = Number(match[1]);
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
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export default function Playground() {
  const [question, setQuestion] = useState("");
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
  const sourceRef = useRef<EventSource | null>(null);

  const busy =
    stage === "request" || stage === "retrieval" || stage === "generation";

  // лог всегда прокручен вниз: ответ печатается вживую
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [answer, asked]);

  // Уходим с экрана посреди генерации — соединение закрываем, иначе
  // бэкенд продолжит гнать поток в никуда.
  useEffect(() => () => sourceRef.current?.close(), []);

  async function ask(text: string) {
    if (!text.trim() || busy) return;

    sourceRef.current?.close();
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

    let messageId: string;
    try {
      const response = await fetch(`${API_BASE}/playground/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ text }),
      });
      if (!response.ok) throw new Error(await response.text());
      messageId = (await response.json()).message_id;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Бэкенд не отвечает");
      setStage("error");
      return;
    }

    const source = new EventSource(
      `${API_BASE}/playground/stream?message_id=${encodeURIComponent(messageId)}`,
    );
    sourceRef.current = source;

    source.addEventListener("retrieval", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setFragments(data.fragments);
      setBelowThreshold(!data.has_answer);
      setStage(data.has_answer ? "generation" : "retrieval");
    });

    source.addEventListener("delta", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setAnswer((current) => current + data.text);
    });

    source.addEventListener("final", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      if (data.text) setAnswer(data.text);
      setTelemetry(data.telemetry);
      setEscalated(data.escalated);
      setStage("done");
      source.close();
    });

    source.addEventListener("error", (event) => {
      // событие error приходит и от сервера (наше), и от самого
      // EventSource при обрыве — различаем по наличию данных
      const raw = (event as MessageEvent).data;
      setError(raw ? JSON.parse(raw).detail : "Соединение прервано");
      setStage("error");
      source.close();
    });
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
              Soro-27B · FP8
            </span>
            <span style={{ color: "var(--muted2)" }}>·</span>
            <span>База знаний: Банк Эсхата</span>
          </div>

          <div className="chatlog" ref={logRef}>
            {!asked && (
              <div className="empty">
                Задайте вопрос или выберите готовый —<br />
                ответ печатается вживую, а справа виден разбор поиска.
              </div>
            )}

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
                </div>
              </div>
            )}
          </div>

          <div className="asks">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                className={preset.danger ? "danger" : ""}
                disabled={busy}
                onClick={() => ask(preset.text)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* Своё поле ввода. В эталоне только кнопки-заготовки, но экран
              называется площадкой и нужен банку, чтобы проверять СВОИ
              вопросы; кнопки эталона при этом остались как были. */}
          <form
            className="asks"
            style={{ borderTop: 0, paddingTop: 0 }}
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
