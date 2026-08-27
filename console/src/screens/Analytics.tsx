import { useCallback, useEffect, useState } from "react";
import { Analytics as Data, getAnalytics } from "../lib/api";
import { Failed } from "../components/State";
import { Delta } from "../components/Intel";
import { AI_RESOLUTION_RATE, CSAT, ESCALATION_RATE, TOP_TOPICS, ru } from "../lib/intel";

// Экран 07 «Аналитика».
//
// Разметка и классы — из эталона (секция an): четыре карточки KPI, донат
// исходов, горизонтальные бары по каналам и языкам, список тем. Цифры
// живые — пять запросов приложения Б, `GET /api/analytics?days=7`.
//
// ТРИ ОТСТУПЛЕНИЯ ОТ ПРОТОТИПА, все по одной причине: экран показывают
// правлению банка, и нарисованное число там становится обещанием.
//
// 1. Карточка «Оценка ответов» показывает настоящую среднюю по пяти
//    баллам — их ставят клиенты после закрытия диалога. Пока оценок нет
//    ни одной, на её месте медиана ответа бота: пустая плашка «—» на
//    месте KPI выглядит поломкой, а медиана считается запросом из того же
//    приложения Б и проверяет норматив «ответ < 6 сек» из раздела 3.
// 2. В донате два сегмента вместо трёх. Третий в прототипе — «ушли · 11%»,
//    но признака «клиент ушёл» в схеме нет: диалог либо закрыт ботом, либо
//    дошёл до оператора. Считать «ушедших» не из чего.
// 3. В барах по каналам нет Instagram: канала нет ни в ТЗ, ни в CHECK
//    `channel_identities.channel`. Появится — строка появится сама.

const CHANNEL_NAMES: Record<string, string> = {
  telegram: "Telegram",
  widget: "Веб-виджет",
  whatsapp: "WhatsApp",
};

const LANGUAGE_NAMES: Record<string, string> = {
  tj: "Таджикский",
  ru: "Русский",
  other: "Другой",
};

// Длина окружности доната в единицах viewBox эталона: 2πr при r = 15.9.
const DONUT = 99.9;

function Bar({
  label,
  value,
  share,
  gold,
}: {
  label: string;
  value: string;
  share: number;
  gold?: boolean;
}) {
  return (
    <div className="hbar">
      <div className="lab">{label}</div>
      <div className="track">
        <div
          className={gold ? "fill gold" : "fill"}
          style={{ width: `${Math.max(share, 2)}%` }}
        />
      </div>
      <div className="val">{value}</div>
    </div>
  );
}

function seconds(ms: number): string {
  // Секунды с одним знаком: миллисекунды на этом экране никому не нужны,
  // а «6 сек» из норматива — как раз та точность, о которой спорят.
  return `${(ms / 1000).toFixed(1).replace(".", ",")} с`;
}

export default function Analytics() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    getAnalytics(7)
      .then(setData)
      .catch(() => setError("Не удалось получить цифры с бэкенда"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const totalChannels =
    data?.channels.reduce((sum, row) => sum + row.conversations, 0) || 0;
  const totalLanguages =
    data?.languages.reduce((sum, row) => sum + row.messages, 0) || 0;
  const share = data?.conversations.bot_share ?? 0;
  const operatorShare = 100 - share;

  return (
    <>
      <div className="head">
        <div>
          <h1>
            Аналитика <em>для правления</em>
          </h1>
          <p>
            Цифры, которые нужны на защите бюджета: сколько обращений сняли с
            людей, где бот ошибается и о чём клиенты спрашивают чаще всего.
          </p>
        </div>
      </div>

      {error && <Failed text={error} onRetry={load} />}

      <div className="grid g4">
        <div className="card">
          <div className="eyebrow">Снято с колл-центра</div>
          <div className="stat rose">{data ? data.conversations.by_bot : "…"}</div>
          <div className="substat">
            {data
              ? `диалогов за ${data.days} дней бот закрыл сам`
              : "считаю по базе"}
          </div>
        </div>

        <div className="card">
          <div className="eyebrow">Экономия времени</div>
          <div className="stat">
            {data ? `≈ ${String(data.hours_saved).replace(".", ",")} ч` : "…"}
          </div>
          <div className="substat">при среднем разговоре 4,5 мин</div>
        </div>

        {/* Пока оценок нет, карточка показывает медиану ответа: пустая
            плашка «—» на месте KPI выглядит поломкой, а медиана считается
            запросом из того же приложения Б и проверяет норматив приёмки.
            Появилась первая оценка — карточка становится оценкой. */}
        {data && data.rating.average !== null ? (
          <div className="card">
            <div className="eyebrow">Оценка ответов</div>
            <div className="stat brass">
              {String(data.rating.average).replace(".", ",")}/5
            </div>
            <div className="substat">
              по {data.rating.total} оценкам клиентов
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="eyebrow">Медиана ответа бота</div>
            <div className="stat brass">
              {data && data.median_latency_ms !== null
                ? seconds(data.median_latency_ms)
                : "—"}
            </div>
            <div className="substat">
              {data && data.median_latency_ms !== null
                ? "норматив приёмки — меньше 6 секунд"
                : "ответов за период не было"}
            </div>
          </div>
        )}

        <div className="card">
          <div className="eyebrow">Исходы диалогов</div>
          <div className="donutwrap">
            <svg width="86" height="86" viewBox="0 0 42 42">
              <circle
                cx="21"
                cy="21"
                r="15.9"
                fill="none"
                stroke="#2B1826"
                strokeWidth="6"
              />
              <circle
                cx="21"
                cy="21"
                r="15.9"
                fill="none"
                stroke="#E8506B"
                strokeWidth="6"
                strokeDasharray={`${(share * DONUT) / 100} ${
                  DONUT - (share * DONUT) / 100
                }`}
                strokeDashoffset="25"
                strokeLinecap="round"
              />
              <circle
                cx="21"
                cy="21"
                r="15.9"
                fill="none"
                stroke="#DCA84C"
                strokeWidth="6"
                strokeDasharray={`${(operatorShare * DONUT) / 100} ${
                  DONUT - (operatorShare * DONUT) / 100
                }`}
                strokeDashoffset={String(25 - (share * DONUT) / 100)}
                strokeLinecap="round"
              />
              <text
                x="21"
                y="23.5"
                textAnchor="middle"
                fontFamily="JetBrains Mono"
                fontSize="8"
                fill="#F9EFF1"
              >
                {data ? `${share}%` : "…"}
              </text>
            </svg>
            <div className="donutlegend">
              <div>
                <i style={{ background: "#E8506B" }} />
                бот · {data ? data.conversations.by_bot : 0}
              </div>
              <div>
                <i style={{ background: "#DCA84C" }} />
                оператор · {data ? data.conversations.by_operator : 0}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="eyebrow">Диалоги по каналам</div>
          {data?.channels.length ? (
            data.channels.map((row) => (
              <Bar
                key={row.channel}
                label={CHANNEL_NAMES[row.channel] || row.channel}
                value={String(row.conversations)}
                share={totalChannels ? (100 * row.conversations) / totalChannels : 0}
              />
            ))
          ) : (
            <div className="substat">за период диалогов не было</div>
          )}

          <div className="eyebrow" style={{ marginTop: 20 }}>
            Язык обращений
          </div>
          {data?.languages.length ? (
            data.languages.map((row) => {
              const percent = totalLanguages
                ? Math.round((100 * row.messages) / totalLanguages)
                : 0;
              return (
                <Bar
                  key={row.lang}
                  label={LANGUAGE_NAMES[row.lang] || row.lang}
                  value={`${percent}%`}
                  share={percent}
                  gold
                />
              );
            })
          ) : (
            <div className="substat">вопросов за период не было</div>
          )}
        </div>

        <div className="card">
          <div className="eyebrow">О чём спрашивают чаще всего</div>
          {data?.top_questions.length ? (
            data.top_questions.map((row) => (
              <div className="qrow" key={row.question}>
                {row.question}
                <span>{row.count}</span>
              </div>
            ))
          ) : (
            <div className="substat">вопросов за период не было</div>
          )}

          <div className="eyebrow" style={{ marginTop: 22 }}>
            Требует внимания
          </div>
          <div className="note">
            {data && data.attention.no_answer > 0 ? (
              <>
                По {data.attention.no_answer} вопросам бот не нашёл ответа в базе
                и передал их оператору. Посмотрите их в инбоксе: если тема
                повторяется — документа по ней в базе знаний либо нет, либо он
                устарел.
              </>
            ) : (
              <>
                За период бот ни разу не сдался из-за отсутствия ответа в базе.
                Это хорошая новость, но проверьте объём: на пустой базе такая же
                картина.
              </>
            )}
          </div>
        </div>
      </div>

      <PeriodChange />
    </>
  );
}

/** Сравнение с прошлым периодом и разбор по четырём срезам.
 *
 *  ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ БЛОК С ПОМЕТКОЙ. Всё выше на экране — живые
 *  запросы к базе стенда. Здесь — смоделированный поток: у стенда просто
 *  нет истории за два сравнимых периода (диалогов за 7 дней восемь), а
 *  сравнивать 8 с 5 бессмысленно. Показываем на объёме, похожем на
 *  банковский, и честно говорим, что это симуляция.
 *
 *  ЗАЧЕМ ВООБЩЕ. Абсолютное число обращений по теме ничего не значит:
 *  «1 248 про комиссии» — много это или мало? Смысл появляется только в
 *  сравнении с прошлой неделей, и именно изменение запускает разбор
 *  причины. */
function PeriodChange() {
  const groups: { title: string; rows: { label: string; value: string; delta: number | null; goodWhenUp: boolean }[] }[] = [
    {
      title: "Клиенты",
      rows: [
        { label: "Обращений", value: "12 043", delta: 12.4, goodWhenUp: true },
        { label: "Удовлетворённость", value: `${ru(CSAT)} / 5`, delta: 3.4, goodWhenUp: true },
        { label: "Повторные обращения", value: "9,1%", delta: -2.6, goodWhenUp: false },
        { label: "Нерешённые вопросы", value: "148", delta: -14.0, goodWhenUp: false },
      ],
    },
    {
      title: "Работа AI",
      rows: [
        { label: "Решено силами AI", value: `${ru(AI_RESOLUTION_RATE)}%`, delta: 8.1, goodWhenUp: true },
        { label: "Передано оператору", value: `${ru(ESCALATION_RATE)}%`, delta: -8.1, goodWhenUp: false },
        { label: "Ответы со ссылкой", value: "88%", delta: 27.0, goodWhenUp: true },
        { label: "Средняя уверенность", value: "0,86", delta: 4.2, goodWhenUp: true },
      ],
    },
    {
      title: "Операции",
      rows: [
        { label: "Нагрузка на оператора", value: "34 диал./смена", delta: -11.5, goodWhenUp: false },
        { label: "Среднее время ответа", value: "1,3 с", delta: -18.0, goodWhenUp: false },
        { label: "Первый ответ человека", value: "2,4 мин", delta: -7.0, goodWhenUp: false },
        { label: "Время решения", value: "11 мин", delta: 3.0, goodWhenUp: false },
      ],
    },
  ];

  return (
    <div className="xsec" style={{ marginTop: 22 }}>
      <h3>Изменение к прошлому периоду</h3>

      <div className="simbar">
        <b>Симулированный поток.</b>
        <span>
          Всё выше на экране — живые запросы к базе стенда. Этот блок показан на
          смоделированном объёме: у стенда нет двух сопоставимых периодов для
          честного сравнения.
        </span>
      </div>

      <div className="xsec">
        <div className="panel" style={{ marginBottom: 14 }}>
          <h4>Темы: что выросло и что упало</h4>
          {TOP_TOPICS.map((topic) => (
            <div
              key={topic.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 11,
                padding: "8px 0",
                borderBottom: "1px solid var(--line-soft)",
                fontSize: 12.5,
              }}
            >
              <span style={{ flex: 1 }}>{topic.title}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--muted2)" }}>
                {topic.conversations.toLocaleString("ru-RU")}
              </span>
              {/* Рост числа жалоб — плохая новость, поэтому goodWhenUp=false. */}
              <Delta value={topic.delta} goodWhenUp={false} />
            </div>
          ))}
        </div>

        <div className="mgrid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(265px, 1fr))" }}>
          {groups.map((group) => (
            <div key={group.title} className="panel">
              <h4>{group.title}</h4>
              {group.rows.map((row) => (
                <div
                  key={row.label}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 9,
                    padding: "7px 0",
                    fontSize: 12,
                  }}
                >
                  <span style={{ flex: 1, color: "var(--muted)" }}>{row.label}</span>
                  <span className="mono" style={{ fontSize: 12 }}>
                    {row.value}
                  </span>
                  <Delta value={row.delta} goodWhenUp={row.goodWhenUp} />
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
