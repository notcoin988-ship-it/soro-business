import { useEffect, useState } from "react";
import { Security, getWorkspace, setSecurity } from "../lib/api";
import Confirm, { ConfirmRequest } from "../components/Confirm";

// Экран 01 «Обзор».
//
// Разметка и классы — из эталона (soro-business-console-2.html, секция ov):
// .grid.g4 с KPI, «Готовность к пилоту», «Контур безопасности». Живым
// сделан только последний блок: четыре переключателя действительно
// управляют поведением бота. Цифры KPI и чек-лист пока статика прототипа —
// под них нужен GET /api/overview, это отдельная задача.
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

export default function Overview() {
  const [security, setState] = useState<Security | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null);

  useEffect(() => {
    getWorkspace()
      .then((info) => setState(info.security))
      .catch(() => setError("Не удалось прочитать настройки воркспейса"));
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
            Банк Эсхата — <em>демо-воркспейс</em>
          </h1>
          <p>
            Изолированное пространство банка: свои документы, свои каналы, свой
            аудит-лог. Данные не пересекаются с другими клиентами и не покидают
            инфраструктуру в Душанбе.
          </p>
        </div>
      </div>

      {/* KPI и чек-лист — разметка эталона, данные пока статические:
          под них нужен GET /api/overview (приложение А), это отдельная
          задача. Оставлены как есть, чтобы экран не «поехал». */}
      <div className="grid g4">
        <Kpi
          label="Диалогов за 7 дней"
          value="1 342"
          sub="Telegram · веб · WhatsApp"
        />
        <Kpi
          label="Закрыто без оператора"
          value="61%"
          sub="819 из 1 342 диалогов"
          tone="rose"
        />
        <Kpi label="Медиана ответа" value="2,4 с" sub="95-й перцентиль — 4,1 с" />
        <Kpi
          label="Ответы со ссылкой на источник"
          value="97%"
          sub="остальные — эскалация оператору"
          tone="brass"
        />
      </div>

      <h3 className="sec">Готовность к пилоту</h3>
      <div className="grid g2">
        <div className="card">
          <Check done title="Документы загружены" hint="4 источника, 1 248 фрагментов" />
          <Check
            done
            title="Telegram-бот подключён"
            hint="@EskhataDemoBot, отвечает на тадж. и рус."
          />
          <Check done title="Веб-виджет выдан" hint="скрипт для вставки на eskhata.tj" />
          <Check
            title="WhatsApp Business API"
            hint="работает песочница; боевой номер — после верификации Meta, 5–10 рабочих дней"
          />
          <Check
            title="Передача в колл-центр"
            hint="интеграция с вашей CRM — обсуждается на этапе пилота"
          />
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

function Kpi({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "rose" | "brass";
}) {
  return (
    <div className="card">
      <div className="eyebrow">{label}</div>
      <div className={`stat${tone ? ` ${tone}` : ""}`}>{value}</div>
      <div className="substat">{sub}</div>
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
