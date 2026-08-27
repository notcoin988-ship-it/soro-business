// Общие блоки AI-аналитики: карточка метрики, находка, разбор причины,
// рекомендация с действиями.
//
// ЗАЧЕМ ОБЩИЕ. Одна и та же находка про комиссию показывается на трёх
// экранах — у руководителя, в отчёте и в Killer Demo. Свёрстанная трижды,
// она трижды и разъедется: где-то забудут уверенность, где-то перепутают
// цвет аномалии. Здесь она одна.

import { useState } from "react";
import {
  ESTIMATED_NOTE,
  Insight,
  Metric,
  Recommendation,
  RootCause,
} from "../lib/intel";

/** Знак и цвет изменения. Рост не всегда хорошо: эскалации и время ответа
 *  лучше, когда падают, — поэтому решает `goodWhenUp`, а не знак числа. */
export function Delta({ value, goodWhenUp }: { value: number | null; goodWhenUp: boolean }) {
  if (value === null) return null;
  const rising = value > 0;
  const good = rising === goodWhenUp;
  const tone = value === 0 ? "flat" : good ? "good" : "bad";
  const sign = value > 0 ? "+" : "";
  return (
    <span className={`delta ${tone}`}>
      {rising ? "▲" : "▼"} {sign}
      {String(value).replace(".", ",")}%
    </span>
  );
}

export function MetricCard({ metric }: { metric: Metric }) {
  // Формула раскрывается по клику: на дашборде она шум, но первый вопрос
  // к любой расчётной величине — «откуда это число», и ответ должен быть
  // под рукой, а не в презентации.
  const [open, setOpen] = useState(false);
  return (
    <div className="mcard">
      <div className="mlabel">{metric.label}</div>
      <div className="mvalue">{metric.value}</div>
      <div className="mfoot">
        <Delta value={metric.delta} goodWhenUp={metric.goodWhenUp} />
        {metric.delta !== null && <span style={{ color: "var(--muted2)" }}>к прошлому периоду</span>}
        {metric.estimated && (
          <span
            className="est"
            title={metric.formula ?? ESTIMATED_NOTE}
            onClick={() => setOpen(!open)}
          >
            ≈ {ESTIMATED_NOTE}
          </span>
        )}
      </div>
      <div className="mhint">{open && metric.formula ? metric.formula : metric.hint}</div>
    </div>
  );
}

const KIND_LABEL: Record<Insight["kind"], string> = {
  anomaly: "Аномалия",
  emerging: "Новая проблема",
  positive: "Позитивный тренд",
};

/** Находка. Свёрнута до заголовка — на дашборде их три, и каждая
 *  развёрнутая занимает экран. Разворачивается по клику. */
export function InsightCard({
  insight,
  defaultOpen = false,
}: {
  insight: Insight;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`insight ${insight.kind} ${open ? "open" : ""}`}>
      <div className="ihead" onClick={() => setOpen(!open)}>
        <span className="ikind">{KIND_LABEL[insight.kind]}</span>
        <span className="ititle">{insight.headline}</span>
        <span className="iconf">уверенность {insight.confidence.toFixed(2).replace(".", ",")}</span>
        <span className="ichev">▾</span>
      </div>

      {open && (
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
      )}
    </div>
  );
}

/** Разбор причины: проблема → основания → причина → эффект → что делать.
 *  Читается сверху вниз как один довод. */
export function RootCauseChain({ data }: { data: RootCause }) {
  return (
    <div className="rca">
      <div className="rrow problem">
        <div className="rdot" />
        <div className="rlbl">Проблема</div>
        <div className="rtext strong">{data.problem}</div>
      </div>
      <div className="rrow">
        <div className="rdot" />
        <div className="rlbl">Основания</div>
        <ul>
          {data.evidence.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>
      <div className="rrow cause">
        <div className="rdot" />
        <div className="rlbl">Причина</div>
        <div className="rtext">{data.cause}</div>
      </div>
      <div className="rrow">
        <div className="rdot" />
        <div className="rlbl">Эффект</div>
        <div className="rtext">{data.impact}</div>
      </div>
      <div className="rrow rec">
        <div className="rdot" />
        <div className="rlbl">Рекомендация</div>
        <div className="rtext strong">{data.recommendation}</div>
      </div>
    </div>
  );
}

type ActionState = "idle" | "prepared" | "done";

/** Рекомендация с кнопками действий.
 *
 *  ПРАВИЛО: AI ничего не выполняет сам. Действие с `approval` переходит в
 *  состояние «подготовлено, ждёт человека» и дальше не двигается — на
 *  стенде это видно буквально, и первый вопрос службы безопасности
 *  («а он сам ничего не разошлёт?») снимается показом, а не обещанием. */
export function RecommendationCard({ data }: { data: Recommendation }) {
  const [states, setStates] = useState<Record<string, ActionState>>({});

  function run(id: string, approval: boolean) {
    setStates((current) => ({ ...current, [id]: approval ? "prepared" : "done" }));
  }

  const waiting = data.actions.some((action) => states[action.id] === "prepared");

  return (
    <div className="rec">
      <div className="rechead">
        <span className="ikind" style={{ color: "var(--rose-hi)", background: "var(--rose-wash)" }}>
          Рекомендация
        </span>
      </div>

      <div className="iblock" style={{ marginBottom: 9 }}>
        <div className="ilbl">Проблема</div>
        <p>{data.problem}</p>
      </div>
      <div className="iblock">
        <div className="ilbl">Причина</div>
        <p>{data.cause}</p>
      </div>

      <div className="recadvice">{data.advice}</div>

      <div className="recactions">
        {data.actions.map((action) => {
          const state = states[action.id] ?? "idle";
          return (
            <button
              key={action.id}
              className={`act ${state}`}
              disabled={state !== "idle"}
              title={action.effect}
              onClick={() => run(action.id, action.approval)}
            >
              <span className="actdot" />
              {state === "idle" && action.label}
              {state === "prepared" && `${action.label} · подготовлено`}
              {state === "done" && `${action.label} · создано`}
            </button>
          );
        })}
      </div>

      <div className="actnote">
        {waiting ? (
          <>
            <b>Требуется подтверждение человека.</b> Soro подготовил материалы, но
            не публикует их и не отправляет клиентам — это делает сотрудник банка.
          </>
        ) : (
          "Действия, затрагивающие клиентов или базу знаний, Soro только готовит — публикует человек."
        )}
      </div>
    </div>
  );
}
