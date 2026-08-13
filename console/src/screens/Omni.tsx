import { ReactNode, useEffect, useRef, useState } from "react";

// Экран 04 «Омниканальность».
//
// Самый презентационный экран демо и единственный, у которого нет бэкенда:
// это сценарий, а не живой диалог. Один клиент — Далер — начинает разговор
// в Telegram по дороге домой, вечером продолжает в виджете на сайте и
// попадает к оператору со всей историей. Показывать это вживую нельзя:
// на встрече нужно две минуты и три устройства, а есть один экран.
//
// Разметка, классы и тайминги — из эталона (soro-business-console-2.html,
// секция om и обработчик #omniplay). Правило ТЗ здесь буквальное:
// повторять попиксельно, ничего не улучшать от себя. Приёмка — две вкладки
// рядом, прототип и консоль.
//
// РАСХОЖДЕНИЕ С ТЕКСТОМ ЗАДАЧИ. Там сказано «до старта Tg активен», но в
// эталоне класс `live` до первого клика не стоит ни на одном устройстве —
// его вешает обработчик кнопки. Сделано как в эталоне: сравнивать будут
// с ним.

type Dev = "Tg" | "Web" | "Op";

interface Beat {
  dev: Dev;
  // Классы `.dm` из эталона: u — клиент, a — бот, `a warn` — бот с
  // предупреждением, sys — системное, op — оператор.
  cls: string;
  html: string;
  // Зажигает пункт чек-листа внизу экрана.
  step?: number;
  // Будит устройство: чистит его лог и переносит на него подсветку.
  wake?: Dev;
}

const OMNI: Beat[] = [
  {
    dev: "Tg",
    cls: "u",
    html: "Салом! Фоизи амонати «Ояндасоз» чанд аст?",
    step: 1,
  },
  {
    dev: "Tg",
    cls: "a",
    html:
      "Салом, Далер! Фоизи солона — <b>14,5%</b>, ҳадди ақал — 500 сомонӣ." +
      '<sup class="cite">1</sup> Мӯҳлат аз 12 то 36 моҳ.',
  },
  {
    dev: "Tg",
    cls: "u",
    html: "Раҳмат! Дома с компьютера посмотрю условия подробнее",
  },
  { dev: "Tg", cls: "sys", html: "19:40 · клиент закрыл Telegram" },
  {
    dev: "Web",
    cls: "sys",
    html:
      "21:05 · Далер авторизовался на eskhata.tj — история подтянулась из " +
      "Telegram",
    step: 2,
    wake: "Web",
  },
  {
    dev: "Web",
    cls: "a",
    html: "С возвращением! Мы остановились на вкладе «Ояндасоз». Продолжим?",
  },
  {
    dev: "Web",
    cls: "u",
    html: "Да. А если сниму деньги через год — процент сгорит?",
  },
  {
    dev: "Web",
    cls: "a",
    html:
      "При досрочном закрытии проценты пересчитываются по ставке «до " +
      'востребования» — <b>0,5% годовых</b>.<sup class="cite">2</sup> ' +
      "Начисленное за полные 12 месяцев сохраняется при закрытии после года.",
  },
  {
    dev: "Web",
    cls: "u",
    html: "Понял. А у меня по зарплатной карте лимит снятия какой?",
    step: 3,
  },
  {
    dev: "Web",
    cls: "a warn",
    html:
      "Лимит зависит от типа вашей карты, а я не имею доступа к данным " +
      "клиентов. Соединяю со специалистом — он уже видит нашу переписку.",
  },
  {
    dev: "Web",
    cls: "sys",
    html: "⤴ передано оператору · контекст: 2 канала, 9 сообщений",
  },
  {
    dev: "Op",
    cls: "sys",
    html: "Новый диалог: Далер Р. · Telegram → веб · тема: вклад + лимит карты",
    step: 4,
    wake: "Op",
  },
  {
    dev: "Op",
    cls: "op",
    html:
      '<span class="from">Оператор Манижа</span>Далер, здравствуйте! Вижу, ' +
      "вы выбираете «Ояндасоз» — отличный выбор. По вашей зарплатной Visa " +
      "Gold лимит снятия — 10 000 сомонӣ в сутки. Оформить вам вклад прямо " +
      "сейчас?",
  },
  {
    dev: "Web",
    cls: "op",
    html:
      '<span class="from">Оператор Манижа</span>Далер, здравствуйте! Вижу, ' +
      "вы выбираете «Ояндасоз». По вашей Visa Gold лимит — 10 000 сомонӣ в " +
      "сутки. Оформить вклад прямо сейчас?",
  },
  { dev: "Web", cls: "u", html: "Ваҳ, быстро! Да, давайте 😊" },
];

// Тайминги эталона. Системные реплики короткие, их читают быстрее — отсюда
// две паузы, а не одна. Числа не подбирались заново: приёмка сравнивает
// скорость с прототипом в соседней вкладке.
const START_DELAY = 350;
const GAP_SYS = 1150;
const GAP_MESSAGE = 1500;
// Кнопка оживает не в момент последней реплики, а чуть позже: иначе она
// мигает раньше, чем человек дочитал финал.
const BUTTON_DELAY = 300;

const DEVICES: { id: Dev; icon: string; color: string; title: string; where: string }[] =
  [
    {
      id: "Tg",
      icon: "TG",
      color: "var(--tg)",
      title: "Telegram · @EskhataDemoBot",
      where: "телефон",
    },
    {
      id: "Web",
      icon: "W",
      color: "var(--rose)",
      title: "Виджет на eskhata.tj",
      where: "компьютер",
    },
    {
      id: "Op",
      icon: "ОП",
      color: "var(--ok)",
      title: "Инбокс оператора",
      where: "колл-центр",
    },
  ];

const STEPS = [
  {
    n: 1,
    title: "Вопрос в Telegram",
    hint: "Далер спрашивает про вклад по дороге домой",
  },
  {
    n: 2,
    title: "Продолжение на сайте",
    hint: "вечером открывает eskhata.tj — виджет помнит диалог",
  },
  {
    n: 3,
    title: "Личный вопрос → эскалация",
    hint: "бот не имеет доступа к счёту и передаёт оператору",
  },
  {
    n: 4,
    title: "Оператор с контекстом",
    hint: "видит всю переписку из обоих каналов, отвечает сразу по делу",
  },
];

// Заглушки пустых логов. У Telegram она своя и только до первого клика:
// после старта его лог заполняется сразу, а Web и Op ещё ждут своей очереди
// и объясняют, чего именно ждут.
const EMPTY: Record<Dev, ReactNode> = {
  Tg: <>Нажмите «Показать сценарий»</>,
  Web: (
    <>
      Диалог придёт сюда,
      <br />
      когда клиент откроет сайт
    </>
  ),
  Op: (
    <>
      Оператор подключится,
      <br />
      если бот не справится
    </>
  ),
};

export default function Omni() {
  // Сколько реплик уже показано. Логи устройств, подсветка и чек-лист из
  // этого числа выводятся, а не хранятся отдельно: в эталоне состояние
  // размазано по DOM тремя мутациями, и повторять это в React — заводить
  // три источника правды, которые разъедутся.
  const [played, setPlayed] = useState(0);
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState(false);

  const timers = useRef<number[]>([]);
  const logs = useRef<Record<Dev, HTMLDivElement | null>>({
    Tg: null,
    Web: null,
    Op: null,
  });

  // Уход с экрана посреди сценария не должен оставлять полтора десятка
  // таймеров, дёргающих setState у размонтированного компонента.
  useEffect(() => stop, []);

  function stop() {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }

  function play() {
    stop();
    setStarted(true);
    setFinished(false);
    setPlayed(0);

    let at = START_DELAY;
    OMNI.forEach((beat, index) => {
      timers.current.push(
        window.setTimeout(() => setPlayed(index + 1), at),
      );
      at += beat.cls === "sys" ? GAP_SYS : GAP_MESSAGE;
    });
    timers.current.push(
      window.setTimeout(() => setFinished(true), at + BUTTON_DELAY),
    );
  }

  const shown = OMNI.slice(0, played);

  // Подсветка устройства едет за последним `wake`. До первого `wake`
  // говорит Telegram — с него начинается разговор.
  const live: Dev | null = started
    ? shown.reduce<Dev>((dev, beat) => beat.wake ?? dev, "Tg")
    : null;

  // Шаг 01 зажигается вместе с кликом, а не с первой репликой: чек-лист
  // объясняет, что сейчас произойдёт.
  const hits = new Set<number>(started ? [1] : []);
  shown.forEach((beat) => beat.step && hits.add(beat.step));

  // Лог прокручиваем к последней реплике: высота окна фиксированная, и
  // без этого разговор уезжает под нижний край.
  useEffect(() => {
    Object.values(logs.current).forEach((log) => {
      if (log) log.scrollTop = log.scrollHeight;
    });
  }, [played]);

  return (
    <>
      <div className="head">
        <div>
          <h1>
            Омниканальность · <em>один диалог, три экрана</em>
          </h1>
          <p>
            Клиент начинает в Telegram по дороге, продолжает на сайте с
            компьютера — и попадает к оператору со всей историей. Ни один канал
            не начинает разговор заново.
          </p>
        </div>
      </div>

      <div className="omnibar">
        <button className="playbtn" onClick={play} disabled={started && !finished}>
          {finished ? "↻  Показать ещё раз" : "▶  Показать сценарий"}
        </button>
        <div className="omninote">
          Один клиент — Далер. Смотрите, как диалог перетекает между экранами.
        </div>
      </div>

      <div className="devices">
        {DEVICES.map((device) => {
          const messages = shown.filter((beat) => beat.dev === device.id);
          return (
            <div
              key={device.id}
              className={live === device.id ? "device live" : "device"}
            >
              <div className="devhead">
                <span
                  className="chico"
                  style={{
                    background: device.color,
                    color: device.id === "Web" ? "#fff" : undefined,
                  }}
                >
                  {device.icon}
                </span>
                {device.title}
                <small>{device.where}</small>
              </div>
              <div
                className="devlog"
                ref={(node) => {
                  logs.current[device.id] = node;
                }}
              >
                {messages.length === 0 &&
                  // Telegram после старта молчит только первые 350 мс —
                  // заглушку «нажмите» там показывать уже нечестно.
                  !(device.id === "Tg" && started) && (
                    <div className="devempty">{EMPTY[device.id]}</div>
                  )}
                {messages.map((beat, index) => (
                  <div
                    key={index}
                    className={"dm " + beat.cls}
                    // Разметка внутри реплик — жирный акцент на ставке,
                    // сноска на документ, подпись оператора — часть
                    // сценария и приходит из константы выше. Пользователь
                    // сюда ничего не вводит: экран презентационный.
                    dangerouslySetInnerHTML={{ __html: beat.html }}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div className="omniline">
        <div className="olsteps">
          {STEPS.map((step) => (
            <div
              key={step.n}
              className={hits.has(step.n) ? "ols hit" : "ols"}
              data-s={step.n}
            >
              <div className="k">{String(step.n).padStart(2, "0")}</div>
              <div>
                <b>{step.title}</b>
                <small>{step.hint}</small>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
