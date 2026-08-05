// Экран 01 — Обзор.
//
// Четыре KPI со спарклайнами, ниже — чек-лист готовности к пилоту и
// тумблеры контура безопасности. В прототипе экран статический, цифры
// подставные; живыми они станут вместе с GET /api/analytics (экран 07).
const KPI = [
  {
    label: "Диалогов за 7 дней",
    value: "1 342",
    tone: "",
    hint: "Telegram · веб · WhatsApp",
    line: "0,24 22,20 44,22 66,15 88,17 110,10 132,12 160,5",
    color: "var(--rose)",
  },
  {
    label: "Закрыто без оператора",
    value: "61%",
    tone: "rose",
    hint: "819 из 1 342 диалогов",
    line: "0,22 22,21 44,18 66,19 88,14 110,12 132,9 160,7",
    color: "var(--rose)",
  },
  {
    label: "Медиана ответа",
    value: "2,4 с",
    tone: "",
    hint: "95-й перцентиль — 4,1 с",
    line: "0,10 22,13 44,11 66,16 88,14 110,18 132,17 160,20",
    color: "var(--brass)",
  },
  {
    label: "Ответы со ссылкой на источник",
    value: "97%",
    tone: "brass",
    hint: "остальные — эскалация оператору",
    line: "0,18 22,16 44,15 66,12 88,11 110,9 132,8 160,6",
    color: "var(--brass)",
  },
];

export default function Overview() {
  return (
    <>
      <div className="head">
        <h1>
          Банк Эсхата — <em>демо-воркспейс</em>
        </h1>
        <p>
          Изолированное пространство банка: свои документы, свои каналы, свой
          аудит-лог. Данные не пересекаются с другими клиентами и не покидают
          инфраструктуру в Душанбе.
        </p>
      </div>

      <div className="grid g4">
        {KPI.map((k) => (
          <div className="card" key={k.label}>
            <div className="eyebrow">{k.label}</div>
            <div className={`stat ${k.tone}`}>{k.value}</div>
            <div className="substat">{k.hint}</div>
            <svg
              className="spark"
              width="100%"
              height="30"
              viewBox="0 0 160 30"
              preserveAspectRatio="none"
            >
              <polyline
                points={k.line}
                fill="none"
                stroke={k.color}
                strokeWidth="1.6"
                opacity=".8"
              />
            </svg>
          </div>
        ))}
      </div>
    </>
  );
}
