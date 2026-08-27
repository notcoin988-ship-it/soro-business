// Экран 11 «Безопасность» — то, что спрашивает служба безопасности банка.
//
// ЗАЧЕМ ОТДЕЛЬНЫЙ ЭКРАН. Переключатели контура безопасности есть на экране
// 01, но там их четыре и они часть обзора. На встрече же безопасность —
// отдельный разговор, и на него нужен отдельный экран, который открывают и
// показывают целиком.
//
// ГЛАВНЫЙ БЛОК — маскирование. Первый вопрос банка про любую LLM звучит
// как «а наши данные не утекут в модель». Отвечать на него словами
// бесполезно: нужно показать вход и выход. Поэтому пары «было → ушло в
// модель» стоят выше списка контролей.
//
// ЧЕСТНОСТЬ ЭКРАНА. Часть контролей работает на стенде по-настоящему
// (маскирование, аудит-лог, изоляция воркспейсов, эскалация), часть — про
// разворачивание у банка (on-premise, роли). Помечаем это, а не выдаём всё
// за включённое: обнаруженная на пилоте неправда стоит дороже.

import { useEffect, useState } from "react";
import { MASKING_SAMPLES, SECURITY_CONTROLS } from "../lib/intel";
import { WorkspaceInfo, getWorkspace } from "../lib/api";

/** Что из списка реально проверяется на стенде, а что — про внедрение. */
const LIVE_ON_STAND = new Set(["pii", "isolation", "audit", "grounding", "human"]);

export default function Security() {
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);

  // Состояние маскирования и аудита читаем у бэкенда: на экране 01 их
  // можно выключить, и показывать здесь неизменную галочку было бы враньём.
  useEffect(() => {
    getWorkspace()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  const maskingOn = info?.security.mask_pii !== false;
  const auditOn = info?.security.audit_log !== false;

  return (
    <div>
      <div className="xhead">
        <div className="xh-main">
          <h1>
            Контур <em>безопасности</em>
          </h1>
          <p>
            Что происходит с данными клиента до того, как их увидит модель, где
            они хранятся и кто имеет к ним доступ.
          </p>
        </div>
      </div>

      <div className="xsec">
        <h3>Персональные данные до модели не доходят</h3>
        <div className="panel">
          <div style={{ fontSize: 12.5, lineHeight: 1.65, marginBottom: 15 }}>
            Номера карт, паспортов, телефонов и счетов вырезаются из текста
            <b> до отправки в модель</b>. Модель видит метку типа данных, а не
            значение — и не может его ни запомнить, ни повторить в ответе.
          </div>

          <div className="masking">
            <div
              className="maskrow"
              style={{ background: "none", border: 0, paddingBottom: 0 }}
            >
              <div className="mklbl" />
              <div className="mklbl">Что написал клиент</div>
              <div className="mkarrow" />
              <div className="mklbl">Что получила модель</div>
            </div>

            {MASKING_SAMPLES.map((sample) => (
              <div key={sample.label} className="maskrow">
                <div className="mklbl">{sample.label}</div>
                <div className="mkval">{sample.original}</div>
                <div className="mkarrow">→</div>
                <div className="mkval out">{sample.masked}</div>
              </div>
            ))}
          </div>

          {!maskingOn && (
            <div className="simbar" style={{ marginTop: 15, marginBottom: 0 }}>
              <b>Маскирование сейчас выключено</b>
              <span>
                Переключатель на экране «Обзор» снят — данные уходят в модель как
                есть. Для банковского контура так работать нельзя.
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="xsec">
        <h3>Контроли</h3>
        <div className="sgrid">
          {SECURITY_CONTROLS.map((control) => {
            // Два контроля отражают реальные переключатели стенда.
            const on =
              control.id === "pii" ? maskingOn : control.id === "audit" ? auditOn : control.on;
            const live = LIVE_ON_STAND.has(control.id);
            return (
              <div key={control.id} className="sctl">
                <span
                  className="stick"
                  style={
                    on
                      ? undefined
                      : {
                          background: "var(--brass-wash)",
                          color: "var(--brass)",
                          borderColor: "var(--brass)",
                        }
                  }
                >
                  {on ? "✓" : "○"}
                </span>
                <div style={{ minWidth: 0 }}>
                  <b>{control.title}</b>
                  <small>{control.note}</small>
                  <div style={{ marginTop: 7 }}>
                    <span
                      className="est"
                      style={
                        live
                          ? {
                              background: "rgba(95,199,158,.1)",
                              color: "var(--ok)",
                              borderColor: "rgba(95,199,158,.3)",
                            }
                          : undefined
                      }
                      title={
                        live
                          ? "Работает на этом стенде — можно проверить прямо сейчас"
                          : "Относится к развёртыванию в контуре банка"
                      }
                    >
                      {live ? "работает на стенде" : "этап внедрения"}
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="xsec">
        <div className="xcols">
          <div className="panel">
            <h4>Что пишется в аудит-лог</h4>
            <ul style={{ margin: 0, paddingLeft: 17, fontSize: 12, lineHeight: 1.85 }}>
              <li>Вопрос клиента — уже в замаскированном виде</li>
              <li>Какие фрагменты документов нашлись и с какой оценкой</li>
              <li>Ответ модели и признак эскалации</li>
              <li>Кто из операторов взял диалог и что ответил</li>
              <li>Время каждого шага</li>
            </ul>
            <div style={{ marginTop: 11, fontSize: 11.5, color: "var(--muted2)" }}>
              Хранение — 3 года. Сейчас аудит-лог{" "}
              <b style={{ color: auditOn ? "var(--ok)" : "var(--brass)" }}>
                {auditOn ? "включён" : "выключен"}
              </b>
              .
            </div>
          </div>

          <div className="panel">
            <h4>Где что находится</h4>
            <ul style={{ margin: 0, paddingLeft: 17, fontSize: 12, lineHeight: 1.85 }}>
              <li>Модель — на серверах в Душанбе, запросы наружу не уходят</li>
              <li>Документы и диалоги — в базе банка, отдельное пространство</li>
              <li>Каждый банк изолирован: чужие документы недоступны</li>
              <li>Ответ строится только по загруженным документам</li>
              <li>Нет ответа в документах — вопрос уходит человеку</li>
            </ul>
            <div style={{ marginTop: 11, fontSize: 11.5, color: "var(--muted2)" }}>
              Модель стенда: <b>{info ? info.model.split("/").pop() : "…"}</b>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
