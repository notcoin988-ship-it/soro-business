import { QRCodeSVG } from "qrcode.react";

// Экран 05 — Каналы. Разметка из прототипа (секция ch) с одной заменой:
// там QR декоративный, рисуется псевдослучайным LCG со seed 20260731 и не
// сканируется. Здесь настоящий, через qrcode.react — библиотека названа в
// ТЗ прямо, согласовывать её не нужно.
const BOT = "EskhataDemoBot";

const SNIPPET = `<script src="https://cdn.sorollm.tj/w.js"
  data-ws="eskhata-demo"
  data-lang="tg,ru"></script>`;

export default function Channels() {
  return (
    <>
      <div className="head">
        <div>
          <h1>
            Каналы <em>подключения</em>
          </h1>
          <p>
            Один бот, одна база знаний, одна история переписки — сколько бы
            каналов ни было включено.
          </p>
        </div>
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
            <QRCodeSVG value={`https://t.me/${BOT}`} size={132} level="M" />
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
              <span
                className="chico"
                style={{ background: "var(--rose)", color: "#fff" }}
              >
                W
              </span>
              Веб-виджет
            </div>
            <span className="pill live">
              <span className="dot" />
              Активен
            </span>
          </div>
          <div className="chdesc">
            Одна строка в шаблон сайта. Цвета и приветствие настраиваются под
            бренд банка.
          </div>
          <div className="snippet">{SNIPPET}</div>
        </div>

        <div className="chcard">
          <div className="chtop">
            <div className="chname">
              <span className="chico" style={{ background: "var(--wa)" }}>
                WA
              </span>
              WhatsApp
            </div>
            <span className="pill wait">
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
