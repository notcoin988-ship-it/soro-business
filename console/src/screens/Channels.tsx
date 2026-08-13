import { useCallback, useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { ChannelCard, ChannelsInfo, getChannels } from "../lib/api";
import { useLang } from "../lib/lang";
import { Failed } from "../components/State";

// Экран 05 — Каналы. Разметка из прототипа (секция ch), но экран рабочий,
// а не витринный: по нему перед встречей проверяют, что каналы живы.
//
// ЧТО БЫЛО НЕ ТАК С ПРОТОТИПОМ. Там на карточках зашиты плашки «Активен»,
// и экран остаётся зелёным ровно тогда, когда он нужнее всего: ngrok
// сменил адрес, Telegram замолчал, а консоль об этом не знает. Теперь
// состояние приходит с бэкенда и означает факт:
//
//   live    — токен есть, вебхук прописан, ошибок доставки нет;
//   wait    — настроено наполовину: вебхук не прописан, нет адреса стенда,
//             временный токен песочницы;
//   off     — канала нет вовсе;
//   unknown — не смогли спросить Telegram (нет сети).
//
// Ещё две замены против эталона, обе от того, что стенд настоящий: QR там
// декоративный (псевдослучайный LCG со seed 20260731, не сканируется) —
// здесь настоящий, через qrcode.react; сниппет ссылался на выдуманный
// cdn.sorollm.tj — здесь он собирается на бэкенде с адресом ЭТОГО стенда,
// потому что скопированный с экрана сниппет обязан работать.

const STATE_LABEL: Record<ChannelCard["state"], string> = {
  live: "Активен",
  wait: "Требует внимания",
  off: "Не подключён",
  unknown: "Состояние неизвестно",
};

const STATE_CLASS: Record<ChannelCard["state"], string> = {
  live: "pill live",
  wait: "pill wait",
  // `.pill.off` в эталоне серая и без акцента — ровно то, что нужно
  // каналу, которого нет: он не проблема, он просто не подключён.
  off: "pill off",
  unknown: "pill wait",
};

const BADGE: Record<string, { label: string; color: string; ink?: string }> = {
  telegram: { label: "TG", color: "var(--tg)" },
  widget: { label: "W", color: "var(--rose)", ink: "#fff" },
  whatsapp: { label: "WA", color: "var(--wa)" },
};

function Head({ card }: { card: ChannelCard }) {
  const { t } = useLang();
  const badge = BADGE[card.id];
  return (
    <div className="chtop">
      <div className="chname">
        <span className="chico" style={{ background: badge.color, color: badge.ink }}>
          {badge.label}
        </span>
        {card.title}
      </div>
      <span className={STATE_CLASS[card.state]}>
        <span className="dot" />
        {t(STATE_LABEL[card.state])}
      </span>
    </div>
  );
}

function Note({ card, days }: { card: ChannelCard; days: number }) {
  return (
    <div className="chnote">
      <div className={card.state === "live" ? "chstate" : "chstate warn"}>
        {card.note}
      </div>
      <div className="substat">
        {card.conversations} диалогов за {days} дней
        {card.webhook && card.webhook.pending > 0
          ? ` · в очереди Telegram ${card.webhook.pending}`
          : ""}
      </div>
    </div>
  );
}

export default function Channels() {
  const { t } = useLang();
  const [info, setInfo] = useState<ChannelsInfo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setInfo(await getChannels());
      setError("");
    } catch {
      setError("Не удалось прочитать состояние каналов");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const find = (id: string) => info?.channels.find((item) => item.id === id);
  const telegram = find("telegram");
  const widget = find("widget");
  const whatsapp = find("whatsapp");
  const days = info?.days ?? 7;

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Буфер обмена закрыт политикой браузера — не беда: сниппет виден
      // и выделяется мышью.
      setError("Браузер не дал доступ к буферу — скопируйте вручную");
    }
  }

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
        {/* Проверка перед встречей — главное действие этого экрана, и она
            должна быть под рукой, а не через F5. */}
        <button className="btn" onClick={load} disabled={busy}>
          {busy ? t("Проверяю…") : "↻  " + t("Проверить")}
        </button>
      </div>

      {error && <Failed text={error} onRetry={load} />}

      <div className="grid g3">
        <div className="chcard">
          {telegram && <Head card={telegram} />}
          <div className="chdesc">
            Отсканируйте — бот ответит по документам банка. Работает прямо на
            встрече, ставить ничего не нужно.
          </div>
          <div className="qrbox">
            {telegram?.link ? (
              <QRCodeSVG value={telegram.link} size={132} level="M" />
            ) : (
              <div className="substat">бот не подключён</div>
            )}
          </div>
          <div
            className="mono"
            style={{ fontSize: 11, color: "var(--muted)", textAlign: "center" }}
          >
            @{telegram?.bot ?? "—"}
          </div>
          {telegram && <Note card={telegram} days={days} />}
        </div>

        <div className="chcard">
          {widget && <Head card={widget} />}
          <div className="chdesc">
            Одна строка в шаблон сайта. Цвета и приветствие настраиваются под
            бренд банка.
          </div>
          <div className="snippet">{widget?.snippet ?? "…"}</div>
          <div className="chactions">
            <button
              className="btn"
              disabled={!widget?.snippet}
              onClick={() => widget?.snippet && copy(widget.snippet)}
            >
              {copied ? t("Скопировано") : t("Скопировать")}
            </button>
            {widget?.site_url && (
              <a className="btn" href={widget.site_url} target="_blank" rel="noopener">
                {t("Показать на сайте")}
              </a>
            )}
          </div>
          {widget && <Note card={widget} days={days} />}
        </div>

        <div className="chcard">
          {whatsapp && <Head card={whatsapp} />}
          <div className="chdesc">
            Работает на тестовом номере. Постоянный токен выпускается через
            System User — временный живёт 24 часа и отвалится в самый неудобный
            момент.
          </div>
          {whatsapp && <Note card={whatsapp} days={days} />}
        </div>
      </div>
    </>
  );
}
