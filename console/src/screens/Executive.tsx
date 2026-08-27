// Экран 09 «Руководству» — дашборд для CEO/COO/CIO.
//
// ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ ЭКРАНА 07. Аналитика показывает, ЧТО происходит:
// диалоги, задержки, темы. Здесь — что это значит для банка: сколько
// сэкономлено, что ухудшилось, что делать. Технических деталей (уверенность
// ретривера, задержки p95, размеры чанков) здесь нет намеренно — их место
// в режиме «Операции».
//
// ГЛАВНЫЙ ЭЛЕМЕНТ — строка «Спросите Soro о бизнесе». Руководитель не
// изучает дашборд, он задаёт вопрос. Ответ обязан приходить с основаниями
// и источником: без них это гадание, а не аналитика.

import { useState } from "react";
import {
  ASK_SUGGESTIONS,
  BUSINESS_IMPACT,
  BusinessAnswer,
  EXECUTIVE_METRICS,
  INSIGHTS,
  RECOMMENDATIONS,
  ROOT_CAUSE,
  REPORT_TYPES,
  TOP_TOPICS,
  askBusiness,
} from "../lib/intel";
import { Delta, InsightCard, MetricCard, RecommendationCard, RootCauseChain } from "../components/Intel";

export default function Executive({ onOpen }: { onOpen?: (screen: string) => void }) {
  return (
    <div>
      <div className="xhead">
        <div className="xh-main">
          <h1>
            Банк <em>в цифрах</em>
          </h1>
          <p>
            Что происходит с клиентским потоком, во что это обходится банку и что
            Soro предлагает сделать. Технические подробности — в режиме «Операции».
          </p>
        </div>
        <div className="xh-side">
          <button className="btn" onClick={() => onOpen?.("ai")}>
            Отчёты AI
          </button>
          <button className="btn primary" onClick={() => onOpen?.("demo")}>
            ▶ Запустить демо
          </button>
        </div>
      </div>

      <SimBar />
      <AskSoro />

      <div className="xsec">
        <h3>Ключевые показатели</h3>
        <div className="mgrid">
          {EXECUTIVE_METRICS.map((metric) => (
            <MetricCard key={metric.id} metric={metric} />
          ))}
        </div>
      </div>

      <div className="xsec">
        <h3>Эффект для бизнеса</h3>
        <div className="mgrid bigimpact">
          {BUSINESS_IMPACT.map((metric) => (
            <MetricCard key={metric.id} metric={metric} />
          ))}
        </div>
      </div>

      <div className="xsec">
        <h3>Что заметил Soro</h3>
        <div className="insights">
          {INSIGHTS.map((insight, index) => (
            <InsightCard key={insight.id} insight={insight} defaultOpen={index === 0} />
          ))}
        </div>
      </div>

      <div className="xsec">
        <div className="xcols wide">
          <div>
            <h3>Темы обращений</h3>
            <div className="panel">
              <Topics />
            </div>
          </div>
          <div>
            <h3>Разбор причины</h3>
            <div className="panel">
              <RootCauseChain data={ROOT_CAUSE} />
            </div>
          </div>
        </div>
      </div>

      <div className="xsec">
        <h3>Что предлагает Soro</h3>
        {RECOMMENDATIONS.map((rec) => (
          <RecommendationCard key={rec.id} data={rec} />
        ))}
      </div>

      <div className="xsec">
        <h3>Последние отчёты</h3>
        <div className="rtypes">
          {REPORT_TYPES.slice(0, 4).map((type) => (
            <button key={type.id} className="rtype" onClick={() => onOpen?.("ai")}>
              <b>{type.title}</b>
              <small>{type.note}</small>
              <span className="rper">{type.period}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Полоса-предупреждение: цифры этого экрана — симуляция, а не выгрузка. */
export function SimBar() {
  return (
    <div className="simbar">
      <b>Демо-среда.</b>
      <span>
        Показатели рассчитаны на смоделированном потоке обращений и служат
        иллюстрацией. Живые данные стенда — на экранах «Обзор» и «Аналитика».
      </span>
    </div>
  );
}

function Topics() {
  const max = Math.max(...TOP_TOPICS.map((topic) => topic.conversations));
  return (
    <div>
      {TOP_TOPICS.map((topic) => (
        <div key={topic.id} style={{ marginBottom: 13 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 9,
              marginBottom: 5,
              fontSize: 12.5,
            }}
          >
            <span style={{ flex: 1 }}>{topic.title}</span>
            <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
              {topic.conversations.toLocaleString("ru-RU")}
            </span>
            {/* Изменение к прошлому периоду — самое важное на этом экране:
                абсолютное число тем ничего не говорит без динамики. */}
            <Delta value={topic.delta} goodWhenUp={false} />
          </div>
          <div style={{ height: 5, borderRadius: 99, background: "var(--line-soft)" }}>
            <div
              style={{
                width: `${(topic.conversations / max) * 100}%`,
                height: "100%",
                borderRadius: 99,
                background:
                  topic.delta > 20
                    ? "linear-gradient(90deg, var(--rose-dim), var(--rose))"
                    : "linear-gradient(90deg, var(--line), var(--muted2))",
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Строка «Спросите Soro о бизнесе» с ответом, основаниями и источником. */
function AskSoro() {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<BusinessAnswer | null>(null);
  const [busy, setBusy] = useState(false);

  function ask(text: string) {
    const value = text.trim();
    if (!value || busy) return;
    setAsked(value);
    setQuestion("");
    setBusy(true);
    setAnswer(null);
    // Небольшая задержка намеренная: мгновенный ответ читается как
    // заранее заготовленный текст, а не как работа модели. Полторы
    // секунды совпадают с реальной задержкой площадки.
    window.setTimeout(() => {
      setAnswer(askBusiness(value));
      setBusy(false);
    }, 1400);
  }

  return (
    <div className="ask">
      <h3>Спросите Soro о бизнесе</h3>

      <form
        className="askrow"
        onSubmit={(event) => {
          event.preventDefault();
          ask(question);
        }}
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Почему выросло число жалоб на этой неделе?"
          aria-label="Вопрос о бизнесе"
        />
        <button className="btn primary" type="submit" disabled={busy || !question.trim()}>
          {busy ? "Считаю…" : "Спросить"}
        </button>
      </form>

      <div className="chips">
        {ASK_SUGGESTIONS.map((text) => (
          <button key={text} className="chip" onClick={() => ask(text)}>
            {text}
          </button>
        ))}
      </div>

      {(busy || answer) && (
        <div className="answer">
          <div style={{ fontSize: 11.5, color: "var(--muted2)", marginBottom: 9 }}>
            {asked}
          </div>

          {busy && (
            <div className="rstage on">
              <span className="sdot" />
              Читаю обращения за период…
            </div>
          )}

          {answer && (
            <>
              <div className="atext">{answer.answer}</div>

              {answer.metrics.length > 0 && (
                <div className="ametrics" style={{ marginBottom: 13 }}>
                  {answer.metrics.map((metric) => (
                    <div key={metric.label} className="ametric">
                      <div className="amv">{metric.value}</div>
                      <div className="aml">{metric.label}</div>
                    </div>
                  ))}
                </div>
              )}

              <div className="agrid">
                <div className="abox">
                  <div className="albl">Основания</div>
                  <ul>
                    {answer.evidence.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
                <div className="abox">
                  <div className="albl">Источники</div>
                  {answer.sources.length === 0 ? (
                    <div style={{ fontSize: 11.5, color: "var(--muted2)" }}>
                      Нет данных за период
                    </div>
                  ) : (
                    <ul>
                      {answer.sources.map((source) => (
                        <li key={source}>{source}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              <div className="recadvice" style={{ marginBottom: 0 }}>
                {answer.recommendation}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
