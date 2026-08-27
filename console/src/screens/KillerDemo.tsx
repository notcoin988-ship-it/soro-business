// Экран 12 «Демо» — полный цикл Soro за 2–3 минуты, одной кнопкой.
//
// ЗАЧЕМ ЭТОТ ЭКРАН СУЩЕСТВУЕТ. Стенд из двенадцати экранов показывает всё,
// но не рассказывает историю: презентатор кликает по меню, а банк смотрит
// на интерфейс вместо смысла. Здесь ровно один сценарий, он идёт сам, и
// после него у зрителя складывается одна фраза — «от разговора с клиентом
// к решению руководства».
//
// ПОЧЕМУ БЕЗ ЖИВЫХ ЗАПРОСОВ. Демо не ходит в модель намеренно. Живой ответ
// приходит за 1–8 секунд (p95 на стенде — 7,3 с), и на встрече эта разница
// ломает ритм: пауза посреди фразы читается как поломка. Сценарий, тайминги
// и реплики зафиксированы в `lib/demoScript.ts`, цифры — из `lib/intel.ts`,
// те же, что на дашборде руководителя.
//
// УПРАВЛЕНИЕ. По умолчанию идёт само, но презентатор в любой момент может
// поставить паузу, перескочить к шагу в полосе прогресса или выйти. Это
// важнее автоматизма: на встрече задают вопросы посреди показа.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  DEMO_STEPS,
  DEMO_TOTAL_MS,
  DemoStep,
  FINAL_CHAIN,
  FINAL_FORMULA,
  OperatorContext,
} from "../lib/demoScript";
import { CONVERSATIONS_TOTAL } from "../lib/intel";

export default function KillerDemo({ onOpen }: { onOpen?: (screen: string) => void }) {
  const [running, setRunning] = useState(false);

  if (running) {
    return <Stage onExit={() => setRunning(false)} onOpen={onOpen} />;
  }

  const minutes = Math.round(DEMO_TOTAL_MS / 60000);

  return (
    <div>
      <div className="dstart">
        <h1>
          Soro <em>AI Banking Intelligence</em>
        </h1>
        <p>
          Полный цикл: клиент задаёт вопрос — Soro отвечает по документам банка,
          безопасно передаёт сложное человеку, разбирает весь поток обращений,
          находит проблему, объясняет причину и готовит отчёт руководству.
        </p>

        <button className="runbtn" onClick={() => setRunning(true)}>
          ▶ Запустить демо Soro
        </button>

        <div style={{ fontSize: 11.5, color: "var(--muted2)" }}>
          {DEMO_STEPS.length} шагов · около {minutes} мин · идёт автоматически,
          можно поставить на паузу
        </div>

        <div className="dsteps">
          {DEMO_STEPS.map((step) => (
            <div key={step.id} className="dstepcard">
              <div className="dsn">
                {step.num} · {step.chip}
              </div>
              <div className="dst">{step.caption}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Полноэкранный прогон. */
function Stage({ onExit, onOpen }: { onExit: () => void; onOpen?: (screen: string) => void }) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const step = DEMO_STEPS[index];
  const last = index === DEMO_STEPS.length - 1;

  const go = useCallback((next: number) => {
    setIndex(next);
    setElapsed(0);
  }, []);

  // Один таймер на весь прогон: тикает раз в 100 мс, двигает полосу и
  // переключает шаг. Отдельный setTimeout на шаг здесь хуже — при паузе
  // его пришлось бы пересоздавать с остатком времени вручную.
  useEffect(() => {
    if (paused || last) return;
    const timer = window.setInterval(() => {
      setElapsed((current) => {
        const next = current + 100;
        if (next >= step.duration) {
          setIndex((position) => Math.min(position + 1, DEMO_STEPS.length - 1));
          return 0;
        }
        return next;
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [paused, last, step.duration, index]);

  // Клавиши презентатора: пробел — пауза, стрелки — шаги, Esc — выход.
  // На встрече кликать мышью в кнопки мелко и заметно, с клавиатуры — нет.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === " ") {
        event.preventDefault();
        setPaused((value) => !value);
      }
      if (event.key === "ArrowRight") go(Math.min(index + 1, DEMO_STEPS.length - 1));
      if (event.key === "ArrowLeft") go(Math.max(index - 1, 0));
      if (event.key === "Escape") onExit();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, go, onExit]);

  return (
    <div className="stage">
      <div className="stagetop">
        <div className="stitle">
          Soro <em>AI Banking Intelligence</em>
        </div>
        <div className="sspacer" />
        <div className="stagectl">
          <button className="btn" onClick={() => setPaused(!paused)}>
            {paused ? "▶ Продолжить" : "❚❚ Пауза"}
          </button>
          <button className="btn" onClick={() => go(Math.max(index - 1, 0))} disabled={index === 0}>
            ← Назад
          </button>
          <button
            className="btn"
            onClick={() => go(Math.min(index + 1, DEMO_STEPS.length - 1))}
            disabled={last}
          >
            Дальше →
          </button>
          <button className="btn" onClick={() => go(0)}>
            ⟲ Сначала
          </button>
          <button className="btn" onClick={onExit}>
            Выйти
          </button>
        </div>
      </div>

      <div className="timebar">
        <div
          className="fill"
          style={{ width: last ? "100%" : `${(elapsed / step.duration) * 100}%` }}
        />
      </div>

      <div className="track">
        {DEMO_STEPS.map((item, position) => (
          <button
            key={item.id}
            className={`tstep ${position === index ? "on" : position < index ? "done" : ""}`}
            onClick={() => go(position)}
          >
            <span className="tn">{item.num}</span>
            {item.chip}
          </button>
        ))}
      </div>

      <div className="caption">{step.caption}</div>

      <div className="stagebody">
        <div className="stagewrap">
          <StepView step={step} onOpen={onOpen} onExit={onExit} />
        </div>
      </div>
    </div>
  );
}

function StepView({
  step,
  onOpen,
  onExit,
}: {
  step: DemoStep;
  onOpen?: (screen: string) => void;
  onExit: () => void;
}) {
  switch (step.kind) {
    case "chat":
      return <ChatStep step={step} />;
    case "escalation":
      return <ChatStep step={step} />;
    case "operator":
      return <OperatorStep context={step.operator!} />;
    case "analytics":
      return <AnalyticsStep step={step} />;
    case "insight":
      return <InsightStep step={step} />;
    case "recommendation":
      return <RecommendationStep step={step} />;
    case "report":
      return <ReportStep step={step} />;
    case "final":
      return <FinalStep onOpen={onOpen} onExit={onExit} />;
    default:
      return null;
  }
}

/** Печать текста по буквам — ответ виден «вживую», как на площадке. */
function useTyped(text: string, speed = 18): string {
  const [shown, setShown] = useState("");
  const ref = useRef(text);

  useEffect(() => {
    ref.current = text;
    setShown("");
    if (!text) return;
    let position = 0;
    const timer = window.setInterval(() => {
      position += 2;
      setShown(text.slice(0, position));
      if (position >= text.length) window.clearInterval(timer);
    }, speed);
    return () => window.clearInterval(timer);
  }, [text, speed]);

  return shown;
}

function ChatStep({ step }: { step: DemoStep }) {
  const escalating = step.kind === "escalation";
  // Ответ начинает печататься не сразу: сначала зритель читает вопрос
  // клиента, иначе оба сообщения появляются одновременно и сливаются.
  const [started, setStarted] = useState(false);
  useEffect(() => {
    setStarted(false);
    const timer = window.setTimeout(() => setStarted(true), 1100);
    return () => window.clearTimeout(timer);
  }, [step.id]);

  // Ссылку [1] вырезаем из печатаемого текста и показываем надстрочным
  // значком — как это делает площадка. Иначе на экране рядом стоят и
  // «[1]», и ¹: одна и та же сноска дважды.
  const source = (step.answer ?? "").replace(/\s*\[\d{1,2}\]/g, "");
  const typed = useTyped(started ? source : "");
  const done = typed.length === source.length;

  return (
    <div className="dchat">
      <div className="phone">
        <div className="phead">
          <span className="pdot" />
          {step.channel} · клиент банка
        </div>
        <div className="plog">
          <div className="bub me">{step.question}</div>
          {started && (
            <div className={`bub bot ${escalating ? "warn" : ""}`}>
              {typed}
              {!done && <span className="cursor" />}
              {done && !escalating && step.fragments && <sup>1</sup>}
            </div>
          )}
        </div>
      </div>

      <div className="xray">
        {escalating && (
          <div className="escalate">
            <span className="eicon">⚠</span>
            <div>
              <b>Персональные данные</b>
              <small>{step.warning}</small>
            </div>
          </div>
        )}

        <div className="xrow">
          <span className="xl">Намерение клиента</span>
          <span className="xv">{step.intent}</span>
        </div>
        <div className="xrow">
          <span className="xl">Язык</span>
          <span className="xv">{step.language}</span>
        </div>
        <div className="xrow">
          <span className="xl">Уверенность</span>
          <span className={`xv ${escalating ? "warn" : "ok"}`}>
            {step.confidence?.toFixed(2).replace(".", ",")}
          </span>
        </div>

        {escalating ? (
          <div className="frag" style={{ borderLeftColor: "var(--brass)" }}>
            <div className="fhead">
              <span className="fname">поиск не выполнялся</span>
            </div>
            <div className="fpass">
              Остаток по счёту — персональные данные конкретного клиента. Их нет
              и не может быть в документах банка, поэтому Soro не ищет ответ и не
              придумывает его, а передаёт диалог оператору.
            </div>
          </div>
        ) : (
          <>
            <div className="xrow">
              <span className="xl">Найдено документов</span>
              <span className="xv">{step.fragments?.length}</span>
            </div>
            {step.fragments?.map((fragment) => (
              <div key={fragment.title} className="frag">
                <div className="fhead">
                  <span className="fname">
                    📄 {fragment.title} · {fragment.page}
                  </span>
                  <span className="fscore">{fragment.score.toFixed(2).replace(".", ",")}</span>
                </div>
                <div className="fpass">{fragment.passage}</div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

const WHO: Record<OperatorContext["history"][number]["from"], string> = {
  client: "клиент",
  bot: "soro",
  operator: "оператор",
};

function OperatorStep({ context }: { context: OperatorContext }) {
  return (
    <div className="opwork">
      <div className="panel" style={{ padding: 0 }}>
        <div
          style={{
            padding: "13px 15px",
            borderBottom: "1px solid var(--line-soft)",
            display: "flex",
            alignItems: "center",
            gap: 9,
            flexWrap: "wrap",
          }}
        >
          <b style={{ fontSize: 13 }}>{context.customer}</b>
          <span className="est" style={{ background: "rgba(42,171,238,.12)", color: "var(--tg)", borderColor: "rgba(42,171,238,.3)" }}>
            {context.channel}
          </span>
          <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--brass)" }}>
            {context.reason}
          </span>
        </div>

        <div className="oplog">
          {context.history.map((message, position) => (
            <div key={position} className={`opmsg ${message.from}`}>
              <div className="opwho">{WHO[message.from]}</div>
              <div className="optext">{message.text}</div>
              <div className="opat">{message.at}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="aipanel">
        <div className="aibox">
          <div className="ailbl">
            <span className="aidot" /> Сводка от AI
          </div>
          <p>{context.summary}</p>
        </div>

        <div className="xrow">
          <span className="xl">Намерение</span>
          <span className="xv">{context.intent}</span>
        </div>
        <div className="xrow">
          <span className="xl">Уверенность</span>
          <span className="xv ok">{context.confidence.toFixed(2).replace(".", ",")}</span>
        </div>

        {context.suggested && (
          <div className="aibox aisuggest">
            <div className="ailbl">
              <span className="aidot" style={{ background: "var(--ok)" }} /> Предлагаемый ответ
            </div>
            <p>{context.suggested}</p>
            {/* Кнопки намеренно НЕАКТИВНЫ: это витрина рабочего места
                оператора внутри презентации, а не сам инбокс. Настоящие,
                работающие кнопки — на экране 06. Кликабельная кнопка,
                которая ничего не делает, на встрече читается как поломка. */}
            <div className="aiactions">
              <button className="act" disabled>
                <span className="actdot" />
                Вставить ответ
              </button>
              <button className="act" disabled>
                <span className="actdot" />
                Сократить
              </button>
              <button className="act" disabled>
                <span className="actdot" />
                Перевести
              </button>
            </div>
            <div className="actnote">
              Soro готовит текст, но <b>не отправляет его сам</b> — отправку делает оператор.
            </div>
          </div>
        )}

        <div className="aibox">
          <div className="ailbl">
            <span className="aidot" style={{ background: "var(--brass)" }} /> Следующий шаг
          </div>
          <p className="aival">{context.nextAction}</p>
        </div>

        <div className="aibox">
          <div className="ailbl">Источники под рукой</div>
          {context.sources.map((source) => (
            <div key={source} className="srcitem">
              <span className="sicon">📄</span>
              {source}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function AnalyticsStep({ step }: { step: DemoStep }) {
  // Счётчик добегает до полного числа за полторы секунды: статичная цифра
  // не передаёт, что система только что прочитала весь массив обращений.
  const [count, setCount] = useState(0);
  useEffect(() => {
    const total = CONVERSATIONS_TOTAL;
    const started = Date.now();
    const timer = window.setInterval(() => {
      const progress = Math.min((Date.now() - started) / 1500, 1);
      // Замедление к концу: линейный счётчик выглядит механическим.
      setCount(Math.round(total * (1 - Math.pow(1 - progress, 3))));
      if (progress >= 1) window.clearInterval(timer);
    }, 40);
    return () => window.clearInterval(timer);
  }, [step.id]);

  const icons = { warn: "⚠", new: "◆", ok: "✓" };

  return (
    <div className="panel">
      <div className="dscan">
        <div className="dcount">{count.toLocaleString("ru-RU")}</div>
        <div className="dclbl">{step.analytics?.headline}</div>

        <div className="dfindings">
          {step.analytics?.findings.map((finding, position) => (
            <div
              key={finding.text}
              className={`dfind ${finding.kind}`}
              style={{ animationDelay: `${1500 + position * 320}ms` }}
            >
              <span className="fi">{icons[finding.kind]}</span>
              {finding.text}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function InsightStep({ step }: { step: DemoStep }) {
  const insight = step.insight!;
  return (
    <div className="insight anomaly open">
      <div className="ihead" style={{ cursor: "default" }}>
        <span className="ikind">Аномалия</span>
        <span className="ititle">{insight.headline}</span>
        <span className="iconf">
          уверенность {insight.confidence.toFixed(2).replace(".", ",")}
        </span>
      </div>
      <div className="ibody">
        <div className="iblock">
          <div className="ilbl">Что произошло</div>
          <p>{insight.what}</p>
        </div>
        <div className="iblock">
          <div className="ilbl">Почему</div>
          <p>{insight.why}</p>
        </div>
        <div className="iblock">
          <div className="ilbl">Чем это оборачивается</div>
          <p>{insight.impact}</p>
        </div>
        <div className="iblock">
          <div className="ilbl">Основания</div>
          <ul className="ievidence">
            {insight.evidence.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function RecommendationStep({ step }: { step: DemoStep }) {
  const rec = step.recommendation!;
  const [done, setDone] = useState<Record<string, boolean>>({});

  return (
    <div className="rec">
      <div className="rechead">
        <span className="ikind" style={{ color: "var(--rose-hi)", background: "var(--rose-wash)" }}>
          Рекомендация
        </span>
      </div>

      <div className="iblock" style={{ marginBottom: 9 }}>
        <div className="ilbl">Проблема</div>
        <p>{rec.problem}</p>
      </div>
      <div className="iblock">
        <div className="ilbl">Причина</div>
        <p>{rec.cause}</p>
      </div>

      <div className="recadvice">{rec.advice}</div>

      <div className="recactions">
        {rec.actions.map((action) => (
          <button
            key={action.label}
            className={`act ${done[action.label] ? (action.approval ? "prepared" : "done") : ""}`}
            onClick={() => setDone((current) => ({ ...current, [action.label]: true }))}
          >
            <span className="actdot" />
            {done[action.label]
              ? `${action.label} · ${action.approval ? "ждёт подтверждения" : "создано"}`
              : action.label}
          </button>
        ))}
      </div>

      <div className="actnote">
        <b>Soro не выполняет банковские операции сам.</b> Задачу он создаёт, а
        публикацию статьи и рассылку клиентам подтверждает человек.
      </div>
    </div>
  );
}

function ReportStep({ step }: { step: DemoStep }) {
  const report = step.report!;
  return (
    <div className="rdoc">
      <div className="rdhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>{report.title}</h2>
          <div className="rdper">{report.period} · сформирован Soro</div>
        </div>
        <div className="chactions" style={{ marginTop: 0 }}>
          {/* Неактивны по той же причине, что кнопки оператора выше: внутри
              презентации это изображение отчёта. Рабочие кнопки — на
              экране 09, и там же сказано, что выгрузка пока не подключена. */}
          <button className="btn" disabled>
            Экспорт PDF
          </button>
          <button className="btn" disabled>
            Экспорт XLSX
          </button>
        </div>
      </div>

      <div className="rdbody">
        <div className="rsec">
          <h4>Ключевые метрики</h4>
          <div className="rmetrics">
            {report.metrics.map((metric) => (
              <div key={metric.label} className="rmetric">
                <div className="rmv">{metric.value}</div>
                <div className="rml">{metric.label}</div>
              </div>
            ))}
          </div>
        </div>

        {report.sections.map((section) => (
          <div key={section.title} className="rsec">
            <h4>{section.title}</h4>
            <ul>
              {section.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function FinalStep({
  onOpen,
  onExit,
}: {
  onOpen?: (screen: string) => void;
  onExit: () => void;
}) {
  return (
    <div className="dfinal">
      <h1>
        От разговора с клиентом
        <em>к решению бизнеса</em>
      </h1>

      <div className="chain">
        {FINAL_CHAIN.map((link, position) => (
          <span key={link} style={{ display: "contents" }}>
            <span
              className={`clink ${position === FINAL_CHAIN.length - 1 ? "last" : ""}`}
              style={{ animationDelay: `${position * 260}ms` }}
            >
              {link}
            </span>
            {position < FINAL_CHAIN.length - 1 && (
              <span className="carrow" style={{ animationDelay: `${position * 260 + 130}ms` }}>
                →
              </span>
            )}
          </span>
        ))}
      </div>

      <div className="formula">
        {FINAL_FORMULA.map((part, position) => (
          <span key={part} style={{ display: "contents" }}>
            <span>{part}</span>
            {position < FINAL_FORMULA.length - 1 && <span className="fplus">+</span>}
          </span>
        ))}
        <span className="feq">=</span>
        <span style={{ fontFamily: "'Unbounded', sans-serif", fontSize: 20, color: "var(--rose-hi)" }}>
          Soro
        </span>
      </div>

      <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
        <button
          className="runbtn"
          style={{ fontSize: 14, padding: "13px 26px" }}
          onClick={() => {
            onExit();
            onOpen?.("ex");
          }}
        >
          Открыть дашборд руководителя
        </button>
        <button className="btn" onClick={onExit}>
          Выйти из демо
        </button>
      </div>
    </div>
  );
}
