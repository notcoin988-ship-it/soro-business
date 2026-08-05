import { QRCodeSVG } from "qrcode.react";

// Экран 05 — Каналы.
//
// В прототипе QR декоративный: псевдослучайный LCG со seed 20260731, он не
// сканируется. Здесь сразу настоящий, через qrcode.react — библиотека
// названа в ТЗ прямо, согласовывать её не нужно.
const BOT = "EskhataDemoBot";
const BOT_URL = `https://t.me/${BOT}`;

export default function Channels() {
  return (
    <>
      <div className="head">
        <h1>
          Каналы <em>подключения</em>
        </h1>
        <p>
          Один бот, одна база знаний, одна история переписки — сколько бы
          каналов ни было включено.
        </p>
      </div>

      <div className="grid g3">
        <div className="chcard">
          <div className="chtop">
            <div className="chname">
              <span className="chico" style={{ background: "var(--tg)" }}>
                TG
              </span>
              Telegram
            </div>
            <span className="pill live">
              <span className="dot" />
              Активен
            </span>
          </div>
          <div className="chdesc">
            Отсканируйте — бот ответит по документам банка. Работает прямо на
            встрече, ставить ничего не нужно.
          </div>
          <div className="qrbox">
            <QRCodeSVG value={BOT_URL} size={132} level="M" />
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--muted)", textAlign: "center" }}
          >
            @{BOT}
          </div>
        </div>

        <div className="chcard">
          <div className="chtop">
            <div className="chname">
              <span className="chico" style={{ background: "var(--rose)" }}>
                W
              </span>
              Веб-виджет
            </div>
            <span className="pill">
              <span className="dot" />
              Готов к встройке
            </span>
          </div>
          <div className="chdesc">
            Один тег на сайт банка. Виджет живёт в iframe: стили сайта его не
            ломают, а он не ломает сайт.
          </div>
          <pre
            className="mono"
            style={{
              background: "var(--panel2)",
              border: "1px solid var(--line)",
              borderRadius: 10,
              padding: 12,
              fontSize: 11,
              color: "var(--muted)",
              overflowX: "auto",
              margin: 0,
            }}
          >
{`<script src="${location.origin}/widget/loader.js"
        data-workspace="eskhata-demo"
        defer></script>`}
          </pre>
        </div>

        <div className="chcard">
          <div className="chtop">
            <div className="chname">
              <span className="chico" style={{ background: "var(--wa)" }}>
                WA
              </span>
              WhatsApp
            </div>
            <span className="pill">
              <span className="dot" />
              Песочница Meta
            </span>
          </div>
          <div className="chdesc">
            Работает на тестовом номере. Постоянный токен выпускается через
            System User — временный живёт 24 часа и отвалится в самый неудобный
            момент.
          </div>
        </div>
      </div>
    </>
  );
}
