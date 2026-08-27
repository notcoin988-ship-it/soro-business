import { useCallback, useEffect, useRef, useState } from "react";
import {
  ConversationCard,
  InboxCard,
  InboxStatus,
  closeConversation,
  getConversation,
  inboxSocket,
  listInbox,
  replyToClient,
  returnToBot,
  takeConversation,
} from "../lib/api";
import { useLang } from "../lib/lang";
import { Empty, Failed } from "../components/State";
import OperatorAI from "../components/OperatorAI";

// Экран 06 «Инбокс оператора» (раздел 8.3 ТЗ).
//
// Разметка и классы — из эталона (секция ib): слева очередь с секциями,
// справа переписка, под ней подсказка оператору. Живое здесь всё: список
// приходит из `GET /api/inbox`, переписка из `GET /api/conversations/{id}`,
// кнопки дёргают take/reply/resolve, а `/ws/inbox` толкает обновления.
//
// ГЛАВНОЕ, ЧТО ДОЛЖЕН ПОКАЗАТЬ ЭКРАН: оператор видит ОДИН диалог с
// сообщениями из разных каналов. Ради этого в схеме заведён
// `conversation`, общий для всех идентичностей контакта, и в шапке
// карточки перечислены все каналы клиента, а у каждой реплики стоит свой.
//
// ДВА ОТСТУПЛЕНИЯ ОТ ПРОТОТИПА:
//
// 1. Вторая секция очереди в эталоне называется «Решено ботом · 819».
//    Инбокс работает с эскалациями, а «решено ботом» — это диалоги, в
//    которых эскалации не было; в очереди оператора их нет и быть не
//    может. Секции здесь те, что отдаёт API: ожидают, в работе, решённые.
//    Сколько диалогов бот закрыл сам — это экран 07.
// 2. Поля ответа в прототипе нет вовсе, хотя кнопка «Вставить подсказку в
//    ответ» на него намекает. Раздел 8.3 требует, чтобы оператор отвечал
//    клиенту из консоли, — поле добавлено.

const SECTIONS: { status: InboxStatus; title: string }[] = [
  { status: "waiting", title: "Ожидают оператора" },
  { status: "active", title: "В работе" },
  { status: "resolved", title: "Решённые" },
];

const CHANNEL_BADGE: Record<string, { label: string; color: string; ink?: string }> = {
  telegram: { label: "TG", color: "var(--tg)" },
  widget: { label: "W", color: "var(--rose)", ink: "#fff" },
  whatsapp: { label: "WA", color: "var(--wa)" },
};

// Причины эскалации из раздела 8.1 — словами, которые что-то значат для
// оператора. «pii_topic» в интерфейсе не показываем никогда.
const REASONS: Record<string, string> = {
  no_answer: "ответа нет в документах банка",
  pii_topic: "нужен доступ к данным клиента",
  user_request: "клиент попросил человека",
  low_confidence: "бот не уверен в ответе",
};

function Badge({ channel }: { channel: string | null }) {
  const badge = CHANNEL_BADGE[channel || ""] || { label: "?", color: "var(--line)" };
  return (
    <span className="chico" style={{ background: badge.color, color: badge.ink }}>
      {badge.label}
    </span>
  );
}

function clock(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function BotMark() {
  return (
    <div className="avatar">
      <svg viewBox="0 0 24 24" width="16" height="16">
        <rect x="2" y="9" width="6" height="6" rx="1.6" fill="#E8506B" />
        <rect x="9" y="2" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
        <rect x="9" y="16" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
        <rect x="16" y="9" width="6" height="6" rx="1.6" fill="#DCA84C" />
      </svg>
    </div>
  );
}

export default function Inbox() {
  const { t } = useLang();
  const [queues, setQueues] = useState<Record<InboxStatus, InboxCard[]>>({
    waiting: [],
    active: [],
    resolved: [],
  });
  const [current, setCurrent] = useState<number | null>(null);
  const [card, setCard] = useState<ConversationCard | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Открытый диалог нужен обработчику сокета, а тот заводится один раз.
  // Через состояние он видел бы только то, что было при подписке.
  const openId = useRef<number | null>(null);
  openId.current = current;

  const logRef = useRef<HTMLDivElement | null>(null);

  const refreshQueues = useCallback(async () => {
    try {
      const [waiting, active, resolved] = await Promise.all([
        listInbox("waiting"),
        listInbox("active"),
        listInbox("resolved"),
      ]);
      setQueues({ waiting, active, resolved });
      setError("");
    } catch {
      setError("Бэкенд недоступен — очередь не обновляется");
    }
  }, []);

  const refreshCard = useCallback(async (id: number) => {
    try {
      setCard(await getConversation(id));
    } catch {
      setError("Не удалось прочитать переписку");
    }
  }, []);

  useEffect(() => {
    refreshQueues();
  }, [refreshQueues]);

  useEffect(() => {
    if (current !== null) refreshCard(current);
  }, [current, refreshCard]);

  // Лента прокручивается к последней реплике. Без этого оператор,
  // открывший длинный диалог, видит его начало — и первое, что делает,
  // это крутит вниз, чтобы понять, о чём вообще речь.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [card]);

  // Живые события. Сокет односторонний: сервер толкает, мы перечитываем.
  // Перечитываем целиком, а не патчим состояние по событию: список
  // короткий, а рассинхронизация в инбоксе стоит дороже лишнего запроса.
  useEffect(() => {
    const socket = inboxSocket();
    socket.onmessage = (message) => {
      const data = JSON.parse(message.data);
      refreshQueues();
      if (data.conversation_id === openId.current) {
        refreshCard(data.conversation_id);
      }
    };
    return () => socket.close();
  }, [refreshQueues, refreshCard]);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await refreshQueues();
      if (current !== null) await refreshCard(current);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text || current === null) return;
    await act(() => replyToClient(current, text));
    setDraft("");
  }

  const escalation = card?.escalation;
  const waitingForOperator = escalation !== null && escalation?.taken_by === null;

  return (
    <>
      <div className="head">
        <div>
          <h1>
            Инбокс <em>оператора</em>
          </h1>
          <p>
            Когда Soro не уверен или клиент просит человека — диалог попадает
            сюда вместе со всей перепиской и найденными документами. Оператор не
            начинает с нуля.
          </p>
        </div>
      </div>

      {error && <Failed text={error} onRetry={refreshQueues} />}

      <div className="inbox">
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          {SECTIONS.map((section) => (
            <div key={section.status}>
              <div className="qhead">
                {t(section.title)} · {queues[section.status].length}
              </div>
              {queues[section.status].map((item) => (
                <div
                  key={item.escalation_id}
                  className={
                    item.conversation_id === current ? "thread on" : "thread"
                  }
                  onClick={() => setCurrent(item.conversation_id)}
                >
                  <Badge channel={item.channel} />
                  <div>
                    <div className="who">
                      {item.display_name || `Гость · ${item.channel || "канал"}`}
                    </div>
                    <div className="prev">{item.preview}</div>
                  </div>
                  <div className="time">{clock(item.last_at)}</div>
                </div>
              ))}
              {queues[section.status].length === 0 && (
                <div className="qempty">{t("пусто")}</div>
              )}
            </div>
          ))}
        </div>

        <div className="card">
          {card === null ? (
            // Пусто в очереди и «диалог не выбран» — разные вещи, и
            // подсказка должна отличаться: в первом случае оператору
            // делать нечего, во втором надо кликнуть слева.
            queues.waiting.length + queues.active.length === 0 ? (
              <Empty
                title="Оператор не нужен"
                hint={
                  "Бот справляется сам: ни один диалог не ждёт человека. " +
                  "Карточка появится здесь, как только бот сдастся или клиент " +
                  "попросит специалиста."
                }
              />
            ) : (
              <Empty title={t("Выберите диалог слева")} />
            )
          ) : (
            <>
              <div className="convhead">
                <Badge channel={card.channels[0] || null} />
                <div>
                  <b style={{ fontSize: 14 }}>
                    {card.display_name || "Гость"}
                  </b>
                  <div
                    className="mono"
                    style={{ fontSize: 11, color: "var(--muted2)" }}
                  >
                    {card.channels.join(" · ")} · {card.messages.length} сообщений
                  </div>
                </div>
                <div className="spacer" />
                <span className={waitingForOperator ? "pill wait" : "pill live"}>
                  <span className="dot" />
                  {escalation === null
                    ? "Отвечает бот"
                    : waitingForOperator
                      ? "Передан оператору"
                      : `В работе · ${escalation?.taken_by}`}
                </span>
                {waitingForOperator && (
                  <button
                    className="btn primary"
                    disabled={busy}
                    onClick={() => act(() => takeConversation(card.conversation_id))}
                  >
                    {t("Взять в работу")}
                  </button>
                )}
                {!waitingForOperator && escalation !== null && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => act(() => returnToBot(card.conversation_id))}
                  >
                    {t("Вернуть боту")}
                  </button>
                )}
                {/* «Закрыть» отличается от «вернуть боту» тем, что
                    разговор заканчивается совсем: клиент получает
                    прощание с просьбой оценить работу специалиста, а его
                    следующее сообщение начнёт новый диалог. */}
                {card.status !== "closed" && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => act(() => closeConversation(card.conversation_id))}
                  >
                    {t("Закрыть диалог")}
                  </button>
                )}
              </div>

              <div className="convlog" ref={logRef}>
                {card.messages.map((message, index) => {
                  if (message.role === "user") {
                    return (
                      <div className="msg u" key={message.id}>
                        {message.text}
                        {/* Время и канал у каждой реплики: в склеенном
                            диалоге видно, что вот это пришло вчера из
                            Telegram, а вот это — сегодня из виджета. */}
                        <span className="msgmeta">
                          {clock(message.created_at)} · {message.channel}
                        </span>
                      </div>
                    );
                  }
                  // Последняя реплика бота перед эскалацией объясняет, за
                  // что оператора позвали. В эталоне эта плашка тоже
                  // висит на ней.
                  const lastAssistant =
                    message.role === "assistant" &&
                    escalation !== null &&
                    !card.messages
                      .slice(index + 1)
                      .some((next) => next.role === "assistant");
                  return (
                    <div className="msg a" key={message.id}>
                      <BotMark />
                      <div className={lastAssistant ? "abody warn" : "abody"}>
                        {message.role === "operator" && (
                          <span className="opfrom">
                            Оператор · {message.channel}
                          </span>
                        )}
                        {message.text}
                        {lastAssistant && (
                          <div className="esc">
                            ⤴ Передан оператору · причина:{" "}
                            {REASONS[escalation!.reason] || escalation!.reason}
                          </div>
                        )}
                        <span className="msgmeta">
                          {clock(message.created_at)} · {message.channel}
                          {message.latency_ms
                            ? ` · ${(message.latency_ms / 1000).toFixed(1)} с`
                            : ""}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="convfoot">
                {/* Разбор диалога стоит ВЫШЕ фрагментов: оператор сперва
                    должен понять, о чём разговор и что делать, и только
                    потом читать выдержки из документов. */}
                <OperatorAI
                  card={card}
                  onInsert={(text) => setDraft((draft ? draft + "\n\n" : "") + text)}
                />

                <div className="eyebrow">Подсказка оператору</div>
                {card.hint.length === 0 ? (
                  <div className="substat">
                    Бот не искал по базе — подсказывать нечем. Так бывает, когда
                    клиент сразу попросил человека.
                  </div>
                ) : (
                  card.hint.map((fragment) => (
                    <div className="hintrow" key={fragment.chunk_id}>
                      <div className="hintsrc">
                        {fragment.title}
                        {fragment.page ? `, стр. ${fragment.page}` : ""}
                      </div>
                      <div className="hinttext">{fragment.text}</div>
                    </div>
                  ))
                )}

                {/* Кнопка именно КОПИРУЕТ текст в поле, без ИИ — так прямо
                    оговорено в разделе 8.3: оператор отвечает сам. */}
                {card.hint.length > 0 && (
                  <button
                    className="btn"
                    style={{ marginTop: 12 }}
                    onClick={() =>
                      setDraft(
                        (draft ? draft + "\n\n" : "") +
                          card.hint.map((f) => f.text).join("\n\n"),
                      )
                    }
                  >
                    Вставить подсказку в ответ
                  </button>
                )}

                <div className="replyrow">
                  <textarea
                    className="text"
                    rows={2}
                    placeholder="Ответ клиенту — уйдёт в тот канал, где он написал последним"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    // Enter отправляет, Shift+Enter переносит строку — как
                    // в любом мессенджере и как в самом виджете. Оператор
                    // отвечает десятками, тянуться мышью к кнопке на
                    // каждый ответ он не должен.
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <button
                    className="btn primary"
                    disabled={busy || !draft.trim()}
                    onClick={send}
                  >
                    {t("Отправить")}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
