// Экран 10 «Отчёты AI» — готовые отчёты вместо ручной выгрузки.
//
// ЧЕМ ОТЛИЧАЕТСЯ ОТ ЭКРАНА 08. Экран 08 («Отчёты») — это разговор: там
// руководитель спрашивает цифры словами, и отвечает живая модель по живой
// базе. Здесь — документ: типовой отчёт с постоянной структурой, который
// можно унести с встречи и показать правлению.
//
// ПОЧЕМУ ГЕНЕРАЦИЯ ИДЁТ ЭТАПАМИ. Отчёт появляется не мгновенно намеренно:
// зритель должен увидеть, ЧТО система делает — читает обращения, ищет
// закономерности, проверяет аномалии. Без этого готовый документ выглядит
// заранее написанным файлом, а не результатом разбора.
//
// ЭКСПОРТ. Бэкенда под выгрузку пока нет, и обещать его кнопкой нельзя:
// нажатие честно говорит, что готовится, и предупреждает, что на стенде
// файл не формируется. Место под настоящий вызов размечено в `lib/api.ts`.

import { useEffect, useRef, useState } from "react";
import {
  REPORT_STAGES,
  REPORT_TYPES,
  ReportKind,
  reportFor,
} from "../lib/intel";
import { Delta } from "../components/Intel";
import { SimBar } from "./Executive";

type Phase = "idle" | "running" | "ready";

export default function AIReports() {
  const [kind, setKind] = useState<ReportKind>("weekly");
  const [phase, setPhase] = useState<Phase>("idle");
  const [stage, setStage] = useState(0);
  const [note, setNote] = useState("");
  const timers = useRef<number[]>([]);

  // Таймеры чистим при уходе с экрана: иначе отчёт «догенерируется» на
  // размонтированном компоненте и React ругается в консоль — а правило
  // приёмки требует чистой консоли.
  useEffect(() => {
    return () => {
      timers.current.forEach((id) => window.clearTimeout(id));
      timers.current = [];
    };
  }, []);

  function generate() {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
    setPhase("running");
    setStage(0);
    setNote("");

    REPORT_STAGES.forEach((_, index) => {
      timers.current.push(
        window.setTimeout(() => setStage(index), index * 900),
      );
    });
    timers.current.push(
      window.setTimeout(() => setPhase("ready"), REPORT_STAGES.length * 900 + 500),
    );
  }

  const type = REPORT_TYPES.find((item) => item.id === kind) ?? REPORT_TYPES[0];

  return (
    <div>
      <div className="xhead">
        <div className="xh-main">
          <h1>
            Отчёты <em>AI</em>
          </h1>
          <p>
            Soro сам читает обращения за период, находит закономерности и
            собирает отчёт со сводкой, причинами и рекомендациями.
          </p>
        </div>
        <div className="xh-side">
          <button className="btn primary" onClick={generate} disabled={phase === "running"}>
            {phase === "running" ? "Готовлю…" : "+ Сформировать отчёт"}
          </button>
        </div>
      </div>

      <SimBar />

      <div className="xsec">
        <h3>Тип отчёта</h3>
        <div className="rtypes">
          {REPORT_TYPES.map((item) => (
            <button
              key={item.id}
              className={`rtype ${item.id === kind ? "on" : ""}`}
              onClick={() => {
                setKind(item.id);
                setPhase("idle");
              }}
            >
              <b>{item.title}</b>
              <small>{item.note}</small>
              <span className="rper">{item.period}</span>
            </button>
          ))}
        </div>
      </div>

      {phase === "idle" && (
        <div className="panel rprogress" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 13, marginBottom: 7 }}>
            <b>{type.title}</b> · {type.period}
          </div>
          <div style={{ fontSize: 12, color: "var(--muted2)", marginBottom: 15 }}>
            Отчёт ещё не сформирован
          </div>
          <button className="btn primary" onClick={generate}>
            + Сформировать отчёт
          </button>
        </div>
      )}

      {phase === "running" && (
        <div className="panel rprogress">
          <div style={{ fontSize: 12.5, marginBottom: 13 }}>
            Готовлю: <b>{type.title}</b>
          </div>
          {REPORT_STAGES.map((label, index) => (
            <div
              key={label}
              className={`rstage ${index === stage ? "on" : index < stage ? "done" : ""}`}
            >
              <span className="sdot" />
              {label}
            </div>
          ))}
        </div>
      )}

      {phase === "ready" && (
        <Document type={type} note={note} onNote={setNote} />
      )}
    </div>
  );
}

function Document({
  type,
  note,
  onNote,
}: {
  type: (typeof REPORT_TYPES)[number];
  note: string;
  onNote: (value: string) => void;
}) {
  return (
    <div className="rdoc">
      <div className="rdhead">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>{type.title}</h2>
          <div className="rdper">
            {type.period} · сформирован Soro · {new Date().toLocaleDateString("ru-RU")}
          </div>
        </div>
        {/* «Поделиться» убрано: почтовой рассылки и публичных ссылок на
            отчёт в системе нет, и делать кнопку под несуществующий канал
            нельзя. Отправка руководителю есть — но в Telegram, и живёт она
            в действиях по рекомендации, где этот путь реально работает.

            Выгрузки оставлены: они реализуемы теми же средствами, что уже
            стоят в образе, и размечены под вызов бэкенда. */}
        <div className="chactions" style={{ marginTop: 0 }}>
          <button className="btn" onClick={() => onNote("pdf")}>
            Экспорт PDF
          </button>
          <button className="btn" onClick={() => onNote("xlsx")}>
            Экспорт XLSX
          </button>
        </div>
      </div>

      {note && (
        <div className="simbar" style={{ margin: "0 19px", marginTop: 15 }}>
          <b>Выгрузка {note.toUpperCase()} на стенде не подключена.</b>
          <span>
            Кнопка размечена под будущий вызов бэкенда — файл здесь не формируется,
            чтобы не выдавать заглушку за готовую функцию.
          </span>
        </div>
      )}

      <div className="rdbody">
        {reportFor(type.id).map((section) => (
          <div key={section.title} className="rsec">
            <h4>{section.title}</h4>

            {section.body && <p>{section.body}</p>}

            {section.metrics && (
              <div className="rmetrics">
                {section.metrics.map((metric) => (
                  <div key={metric.label} className="rmetric">
                    <div className="rmv">{metric.value}</div>
                    <div className="rml">{metric.label}</div>
                    {metric.delta !== undefined && (
                      <div style={{ marginTop: 5 }}>
                        <Delta
                          value={metric.delta}
                          goodWhenUp={
                            // Эскалации и время ответа лучше, когда падают.
                            !/Эскалац|Ответ/.test(metric.label)
                          }
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {section.bullets && (
              <ul>
                {section.bullets.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
