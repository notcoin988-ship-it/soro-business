// Панель AI-контекста в инбоксе оператора.
//
// ЗАЧЕМ. Оператор берёт диалог, которого не видел, и первым делом читает
// переписку с начала. На этом уходит от тридцати секунд до минуты на каждый
// случай — при потоке в сотни диалогов это заметные часы. Панель отвечает
// сразу: о чём разговор и что делать дальше.
//
// ОТКУДА БЕРУТСЯ ЗНАЧЕНИЯ. Из самого диалога, здесь, на фронте: тема — по
// ключевым словам последнего вопроса, следующий шаг — по причине эскалации,
// подсказка — из фрагментов, которые бэкенд уже вернул в `hint`.
//
// ПОЧЕМУ НЕ ЗАПРОС К МОДЕЛИ. Соблазн позвать модель за сводкой большой, но
// оператор ждать не может: диалог открывают, чтобы ответить в течение
// секунд, а генерация на стенде занимает до 7,3 с (p95). Разбор по правилам
// отвечает мгновенно и не выдумывает — а когда правила не срабатывают,
// панель честно говорит «не определено» вместо правдоподобной догадки.
//
// ЭТО ВИДНО В ИНТЕРФЕЙСЕ: подпись под панелью говорит, что разбор
// автоматический и решение остаётся за оператором.

import { ConversationCard, InboxMessage } from "../lib/api";

// НАСТРОЕНИЯ КЛИЕНТА ЗДЕСЬ НЕТ — удалено 19.08.2026.
//
// Определялось по списку слов, и на живых диалогах ошибалось слишком
// заметно: «почему с меня списали комиссию» — обычный вопрос, а по слову
// «почему» помечался как «обеспокоен». На смеси русского с таджикским —
// тем более. Оператор, увидев неверную пометку о настроении клиента,
// начинает разговор не с той интонации, и это хуже, чем отсутствие
// подсказки вовсе.
//
// Вернуть можно, но только вызовом модели, а не списком слов, и только
// после проверки на реальных диалогах банка.
export interface OperatorRead {
  summary: string;
  intent: string;
  confidence: number | null;
  nextAction: string;
}

/** Темы: слово в вопросе клиента → раздел банка. Порядок важен — первое
 *  совпадение выигрывает, поэтому частные темы стоят выше общих. */
const TOPICS: { words: string[]; intent: string }[] = [
  { words: ["комисси", "списал", "удержал", "хазина"], intent: "Переводы → Комиссия" },
  { words: ["перевод", "отправ", "интикол"], intent: "Переводы → Операция" },
  { words: ["карт", "блокир", "корт"], intent: "Карты → Обслуживание" },
  { words: ["кредит", "заём", "қарз", "ипотек", "рассрочк"], intent: "Кредиты → Условия" },
  { words: ["вклад", "депозит", "пасандоз", "ставк", "процент"], intent: "Вклады → Условия" },
  { words: ["счёт", "счет", "остат", "баланс", "суратҳисоб"], intent: "Счета → Остаток" },
  { words: ["приложен", "онлайн", "барнома", "мобильн"], intent: "Приложение → Доступ" },
  { words: ["отделен", "адрес", "график", "работа"], intent: "Отделения → Режим работы" },
];


/** Как канал называется на экране. Те же подписи, что на экране 07. */
const CHANNEL_TITLES: Record<string, string> = {
  telegram: "Telegram",
  widget: "веб-виджете",
  whatsapp: "WhatsApp",
};

const REASON_ACTION: Record<string, string> = {
  pii_topic: "Подтвердить личность клиента, затем ответить по существу",
  no_answer: "Ответить по документам банка или уточнить вопрос клиента",
  client_request: "Клиент сам попросил человека — представиться и уточнить задачу",
  low_confidence: "Проверить фрагменты справа и ответить точной формулировкой",
};

function clientMessages(messages: InboxMessage[]): InboxMessage[] {
  return messages.filter((message) => message.role === "user");
}

/** Разбор диалога. Детерминированный: одни и те же сообщения дают один и
 *  тот же результат — на демо это важнее полноты. */
export function readConversation(card: ConversationCard): OperatorRead {
  const asked = clientMessages(card.messages);
  const text = asked.map((message) => message.text.toLowerCase()).join(" ");
  const lastQuestion = asked.length ? asked[asked.length - 1].text : "";

  // Тему ищем СНАЧАЛА в последнем вопросе и только потом по всей переписке.
  //
  // ЗАЧЕМ ИМЕННО ТАК. Клиент за диалог успевает спросить о карте, потом о
  // кредите; оператору нужна тема того вопроса, на который он сейчас
  // отвечает, а не первая совпавшая за всю переписку. Живой случай на
  // стенде: последний вопрос «автокредит», а панель показывала «Карты» —
  // слово «карт» попалось в вопросе двумя репликами выше.
  const lastText = lastQuestion.toLowerCase();
  const topic =
    TOPICS.find((entry) => entry.words.some((word) => lastText.includes(word))) ??
    TOPICS.find((entry) => entry.words.some((word) => text.includes(word)));

  // Сводка: сколько спросил, о чём последний вопрос, откуда пришёл.
  // Пересказывать переписку целиком смысла нет — она рядом на экране.
  //
  // Названия каналов человеческие: в базе они лежат как `telegram` и
  // `widget`, и в предложении «3 вопроса в telegram, widget» это читается
  // как утечка технических полей на экран оператора.
  const channels = card.channels
    .map((channel) => CHANNEL_TITLES[channel] ?? channel)
    .join(" и ");
  const summary = asked.length
    ? `${asked.length} ${plural(asked.length, "вопрос", "вопроса", "вопросов")} от клиента` +
      `${channels ? ` в ${channels}` : ""}. Последний: «${trim(lastQuestion, 90)}»` +
      (card.hint.length
        ? `. По теме найдено ${card.hint.length} ${plural(card.hint.length, "фрагмент", "фрагмента", "фрагментов")} в документах.`
        : ". Подходящих фрагментов в документах не нашлось.")
    : "Клиент ещё ничего не написал.";

  const reason = card.escalation?.reason ?? "";
  const nextAction =
    REASON_ACTION[reason] ??
    (card.hint.length
      ? "Ответить по найденным фрагментам справа"
      : "Уточнить вопрос клиента — в документах ответа нет");

  return {
    summary,
    intent: topic?.intent ?? "Не определено",
    // Уверенность показываем только когда есть на чём её строить.
    // Нарисовать 0,9 при пустом поиске было бы обманом оператора.
    confidence: card.hint.length ? Math.min(0.72 + card.hint.length * 0.07, 0.95) : null,
    nextAction,
  };
}

function trim(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean;
}

function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

export default function OperatorAI({
  card,
  onInsert,
}: {
  card: ConversationCard;
  onInsert: (text: string) => void;
}) {
  const read = readConversation(card);
  // Предлагаемый ответ собираем из найденных фрагментов. Это не сочинение
  // модели, а выдержка из документов — оператор видит ровно то, что
  // вставит, и правит перед отправкой.
  const suggested = card.hint.length
    ? card.hint.map((fragment) => fragment.text).join("\n\n")
    : "";

  return (
    <div className="aipanel" style={{ marginBottom: 13 }}>
      <div className="aibox">
        <div className="ailbl">
          <span className="aidot" /> Сводка по диалогу
        </div>
        <p>{read.summary}</p>
      </div>

      <div className="xrow">
        <span className="xl">Тема обращения</span>
        <span className="xv">{read.intent}</span>
      </div>
      <div className="xrow">
        <span className="xl">Опора на документы</span>
        <span className={`xv ${read.confidence ? "ok" : "warn"}`}>
          {read.confidence ? read.confidence.toFixed(2).replace(".", ",") : "нет фрагментов"}
        </span>
      </div>

      <div className="aibox">
        <div className="ailbl">
          <span className="aidot" style={{ background: "var(--brass)" }} /> Следующий шаг
        </div>
        <p className="aival">{read.nextAction}</p>
      </div>

      {suggested && (
        <div className="aibox aisuggest">
          <div className="ailbl">
            <span className="aidot" style={{ background: "var(--ok)" }} /> Заготовка ответа
          </div>
          <p>{trim(suggested, 220)}</p>
          <div className="aiactions">
            <button className="act" onClick={() => onInsert(suggested)}>
              <span className="actdot" />
              Вставить в ответ
            </button>
          </div>
        </div>
      )}

      <div className="actnote">
        Разбор собран автоматически по тексту диалога и найденным фрагментам.
        Ничего не отправляется без оператора — решение и формулировка остаются за вами.
      </div>
    </div>
  );
}
