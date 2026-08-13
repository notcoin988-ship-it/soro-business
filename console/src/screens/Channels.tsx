import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { WorkspaceInfo, getWorkspace } from "../lib/api";

// Экран 05 — Каналы. Разметка из прототипа (секция ch) с двумя заменами,
// обе от того, что стенд настоящий:
//
// 1. QR в прототипе декоративный — псевдослучайный LCG со seed 20260731,
//    не сканируется. Здесь настоящий, через qrcode.react (библиотека
//    названа в ТЗ прямо, согласовывать не нужно).
// 2. Сниппет в прототипе ссылается на cdn.sorollm.tj/w.js. Такого адреса
//    нет: loader.js отдаёт сам бэкенд по /w.js. На демо стенд живёт за
//    ngrok, и адрес меняется при каждом перезапуске туннеля — поэтому он
//    приходит с сервера, а не зашит здесь. Скопированный с экрана сниппет
//    обязан работать, иначе он хуже, чем никакого.
const FALLBACK_BOT = "EskhataDemoBot";

function snippet(base: string, slug: string): string {
  return `<script src="${base}/w.js"\n  data-ws="${slug}"\n  data-lang="tg,ru"></script>`;
}

export default function Channels() {
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);

  useEffect(() => {
    getWorkspace()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);

  const bot = info?.telegram_bot || FALLBACK_BOT;
  // Пока воркспейс не приехал, показываем адрес самой консоли: она
  // открыта с того же стенда, так что это не выдумка, а верная догадка.
  const base = info?.public_base_url || window.location.origin;

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
            <QRCodeSVG value={`https://t.me/${bot}`} size={132} level="M" />
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--muted)", textAlign: "center" }}
          >
            @{bot}
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
          <div className="snippet">
            {snippet(base, info?.slug || "eskhata-demo")}
          </div>
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
