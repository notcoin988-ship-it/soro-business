import { useEffect, useState } from "react";
import {
  OverviewData,
  Security,
  getOverview,
  getWorkspace,
  setSecurity,
} from "../lib/api";
import Confirm, { ConfirmRequest } from "../components/Confirm";

// Экран 01 «Обзор».
//
// Разметка и классы — из эталона (soro-business-console-2.html, секция ov):
// .grid.g4 с KPI, «Готовность к пилоту», «Контур безопасности». Живое
// здесь всё: KPI и чек-лист приходят из `GET /api/overview`, переключатели
// пишут в `workspaces.settings`.
//
// ЧТО БЫЛО РАНЬШЕ И ПОЧЕМУ ЭТО ЧИНИЛОСЬ: цифры KPI и чек-лист стояли
// статикой прототипа — 1 342 диалога, 61%, галочки напротив всех каналов.
// Это первый экран, который видит правление банка; нарисованное число на
// нём становится обещанием, а галочка напротив неподключённого WhatsApp —
// обещанием того, чего нет вовсе.
//
// В эталоне переключатель — это `div.sw`, у которого клик просто дёргает
// класс `on` и никуда не сохраняется. Здесь состояние живёт в
// `workspaces.settings` и переживает перезагрузку страницы.

interface Toggle {
  key: keyof Security;
  title: string;
  hint: string;
  // «Отвечать только по базе знаний» — это и есть продукт, а не настройка;
  // выключить нельзя, но показать надо: служба безопасности банка смотрит
  // именно на этот список
  locked?: boolean;
}

const TOGGLES: Toggle[] = [
  {
    key: "kb_only",
    title: "Отвечать только по базе знаний",
    hint: "Модель не додумывает: если ответа нет в документах — эскалация оператору.",
    locked: true,
  },
  {
    key: "cite_sources",
    title: "Ссылка на источник в каждом ответе",
    hint: "Название документа и страница. Клиент и проверяющий видят, откуда взят ответ.",
  },
  {
    key: "audit_log",
    title: "Аудит-лог всех обращений",
    hint: "Запрос, найденные фрагменты, ответ, оператор. Хранение — 3 года.",
  },
  {
    key: "mask_pii",
    title: "Маскирование персональных данных",
    hint: "Номера карт, паспорта и телефоны вырезаются до отправки в модель.",
  },
];

// Что будет, если выключить. Показывается в подтверждении — человек должен
// понимать последствие до того, как согласится, а не после.
const CONSEQUENCE: Record<string, string> = {
  cite_sources:
    "Клиент перестанет видеть, из какого документа взят ответ. " +
    "Проверить ответ по источнику будет нельзя.",
  audit_log:
    "Обращения перестанут записываться. За период, пока лог выключен, " +
    "восстановить, что спрашивали и что бот отвечал, будет невозможно.",
  mask_pii:
    "Номера карт, паспортов и телефоны пойдут в модель как есть — " +
    "и попадут в логи её провайдера.",
};

// Секунды с одним знаком: миллисекунды на этом экране никому не нужны, а
// «6 секунд» из норматива приёмки — как раз та точность, о которой спорят.
function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1).replace(".", ",")} с`;
}

export default function Overview() {
  const [security, setState] = useState<Security | null>(null);
  // Имя банка приходит из воркспейса, а не зашито в разметку: в эталоне
  // банк был один, а переключатель на верхней панели заводит новые.
  const [wsName, setWsName] = useState("");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);

  useEffect(() => {
    getWorkspace()
      .then((info) => {
        setState(info.security);
        setWsName(info.name);
      })
      .catch(() => setError("Не удалось прочитать настройки воркспейса"));
    getOverview()
      .then(setOverview)
      .catch(() => setError("Не удалось прочитать состояние стенда"));
  }, []);

  async function save(key: keyof Security, value: boolean) {
    setBusy(true);
    try {
      const result = await setSecurity({ [key]: value });
      setState(result.security);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  function toggle(item: Toggle) {
    if (item.locked || busy || !security) return;
    const next = !security[item.key];

    // Включение — сразу, выключение — с подтверждением: снять защиту
    // одним промахом мыши нельзя.
    if (next) {
      void save(item.key, true);
      return;
    }
    setConfirm({
      title: `Выключить: ${item.title.toLowerCase()}?`,
      text: CONSEQUENCE[item.key] ?? "",
      okLabel: "Выключить",
      onOk: () => void save(item.key, false),
    });
  }

  return (
    <>
      <div className="head">
        <div>
          <h1>
            {wsName || "…"} — <em>демо-воркспейс</em>
          </h1>
          <p>
            Изолированное пространство банка: свои документы, свои каналы, свой
            аудит-лог. Данные не пересекаются с другими клиентами и не покидают
            инфраструктуру в Душанбе.
          </p>
        </div>
      </div>

      <div className="grid g4">
        <Kpi
          label={`Диалогов за ${overview?.days ?? 7} дней`}
          value={overview ? String(overview.conversations.total) : "…"}
          sub={
            overview?.conversations.channels.length
              ? overview.conversations.channels.join(" · ")
              : "за период писем не было"
          }
        >
          <Spark points={overview?.conversations.spark ?? []} color="#E8506B" fill />
        </Kpi>

        <Kpi
          label="Закрыто без оператора"
          value={overview ? `${overview.conversations.bot_share}%` : "…"}
          sub={
            overview
              ? `${overview.conversations.by_bot} из ${overview.conversations.total} диалогов`
              : "считаю по базе"
          }
          tone="rose"
        >
          <Spark
            points={overview?.conversations.spark_bot_share ?? []}
            color="#E8506B"
          />
        </Kpi>

        <Kpi
          label="Медиана ответа"
          value={
            overview?.latency.median_ms != null
              ? seconds(overview.latency.median_ms)
              : "—"
          }
          sub={
            overview?.latency.p95_ms != null
              ? `95-й перцентиль — ${seconds(overview.latency.p95_ms)}`
              : "ответов за период не было"
          }
        >
          <Spark points={overview?.latency.spark ?? []} color="#DCA84C" />
        </Kpi>

        <Kpi
          label="Ответы со ссылкой на источник"
          value={overview ? `${overview.citations.share}%` : "…"}
          sub={
            overview
              ? `${overview.citations.cited} из ${overview.citations.answers} ответов`
              : "остальные — эскалация оператору"
          }
          tone="brass"
        >
          <Spark points={overview?.citations.spark ?? []} color="#DCA84C" />
        </Kpi>
      </div>

      <h3 className="sec">Готовность к пилоту</h3>
      <div className="grid g2">
        <div className="card">
          {/* Чек-лист считается на бэкенде: галочка напротив
              неподключённого канала — худшее, что может быть на первом
              экране, который смотрит правление банка. */}
          {(overview?.readiness ?? []).map((item) => (
            <Check
              key={item.title}
              done={item.done}
              title={item.title}
              hint={item.hint}
            />
          ))}
          {overview === null && (
            <div className="substat">читаю состояние стенда…</div>
          )}
        </div>

        <div className="card">
          <div className="eyebrow">Контур безопасности</div>
          {TOGGLES.map((item) => {
            const on = security ? security[item.key] : true;
            return (
              <div className="tog" key={item.key}>
                <div
                  className={`sw${on ? " on" : ""}${item.locked ? " locked" : ""}`}
                  role="switch"
                  aria-checked={on}
                  aria-label={item.title}
                  aria-disabled={item.locked || busy}
                  tabIndex={item.locked ? -1 : 0}
                  title={
                    item.locked
                      ? "Основа продукта — отключить нельзя"
                      : on
                        ? "Выключить"
                        : "Включить"
                  }
                  onClick={() => toggle(item)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      toggle(item);
                    }
                  }}
                />
                <div>
                  <b>{item.title}</b>
                  <small>{item.hint}</small>
                </div>
              </div>
            );
          })}
          {error && (
            <div className="fail" style={{ marginTop: 10 }}>
              {error}
            </div>
          )}
        </div>
      </div>

      <Confirm request={confirm} onClose={() => setConfirm(null)} />
    </>
  );
}

// Спарклайн строится из ряда чисел по дням. Ряд обязан приходить с
// нулями за дни без диалогов — иначе провал в переписке превратится в
// ровный участок, и линия соврёт. За это отвечает generate_series в
// `api/overview.py`.
function Spark({
  points,
  color,
  fill,
}: {
  points: number[];
  color: string;
  fill?: boolean;
}) {
  if (points.length < 2) return null;

  // Верх и низ считаем по самому ряду: у процентов и миллисекунд разные
  // масштабы, и общей шкалы для них нет.
  const top = Math.max(...points);
  const bottom = Math.min(...points);
  const span = top - bottom || 1;
  const step = 160 / (points.length - 1);
  // 4 и 26 вместо 0 и 30 — чтобы линия не липла к краям карточки.
  const line = points
    .map((value, index) => {
      const y = 26 - ((value - bottom) / span) * 22;
      return `${Math.round(index * step)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      className="spark"
      width="100%"
      height="30"
      viewBox="0 0 160 30"
      preserveAspectRatio="none"
    >
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        opacity=".8"
      />
      {fill && (
        <polyline points={`${line} 160,30 0,30`} fill="rgba(232,80,107,.09)" stroke="none" />
      )}
    </svg>
  );
}

function Kpi({
  label,
  value,
  sub,
  tone,
  children,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "rose" | "brass";
  children?: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="eyebrow">{label}</div>
      <div className={`stat${tone ? ` ${tone}` : ""}`}>{value}</div>
      <div className="substat">{sub}</div>
      {children}
    </div>
  );
}

function Check({
  done,
  title,
  hint,
}: {
  done?: boolean;
  title: string;
  hint: string;
}) {
  return (
    <div className="check">
      <div className={`tick ${done ? "done" : "todo"}`}>{done ? "✓" : "○"}</div>
      <div>
        <b>{title}</b>
        <small>{hint}</small>
      </div>
    </div>
  );
}
