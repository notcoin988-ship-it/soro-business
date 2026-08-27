import { Fragment, useCallback, useEffect, useState } from "react";
import {
  ApiError,
  WorkspaceInfo,
  WorkspaceRow,
  addWorkspace,
  currentWorkspace,
  getWorkspace,
  inboxCounters,
  listWorkspaces,
  selectWorkspace,
} from "./lib/api";
import { useLang } from "./lib/lang";
import Overview from "./screens/Overview";
import Knowledge from "./screens/Knowledge";
import Playground from "./screens/Playground";
import Omni from "./screens/Omni";
import Channels from "./screens/Channels";
import Inbox from "./screens/Inbox";
import Analytics from "./screens/Analytics";
import Executive from "./screens/Executive";
import AIReports from "./screens/AIReports";
import Security from "./screens/Security";
import KillerDemo from "./screens/KillerDemo";
import Offline from "./components/Offline";

// Каркас консоли повторяет прототип: topbar, боковая навигация с номерами
// экранов и футером параметров, справа — экран. Идентификаторы те же, что
// data-go в soro-business-console-2.html.
//
// ГРУППЫ И РЕЖИМЫ. Экранов одиннадцать, плоским списком они больше не
// читаются: оператор и председатель правления ищут в нём разное. Поэтому
// список разбит на три группы, а переключатель в шапке выбирает, чьими
// глазами смотреть на стенд.
//
// «Операции» — работа: документы, диалоги, каналы, инбокс, техника ответов.
// «Руководству» — смысл: сколько стоит, что ухудшилось, что делать.
//
// Экраны 01–07 и их номера НЕ ТРОНУТЫ: ТЗ ссылается на них номерами
// («экран 03», «экран 06»), и переставить их — значит разойтись с
// документом, по которому стенд принимают. Номера 08+ свободны: экраны
// сверх ТЗ, на них документ не ссылается.
type Mode = "ops" | "exec";

interface Screen {
  id: string;
  num: string;
  title: string;
  Component: React.ComponentType<{ onOpen?: (screen: string) => void }>;
  /** В каких режимах виден. Пусто — виден всегда. */
  modes?: Mode[];
  group: string;
  accent?: boolean;
}

const SCREENS: Screen[] = [
  { id: "ov", num: "01", title: "Обзор", Component: Overview, group: "Работа" },
  { id: "kb", num: "02", title: "База знаний", Component: Knowledge, group: "Работа", modes: ["ops"] },
  { id: "pg", num: "03", title: "Площадка", Component: Playground, group: "Работа", modes: ["ops"] },
  { id: "om", num: "04", title: "Омниканальность", Component: Omni, group: "Работа" },
  { id: "ch", num: "05", title: "Каналы", Component: Channels, group: "Работа", modes: ["ops"] },
  { id: "ib", num: "06", title: "Инбокс оператора", Component: Inbox, group: "Работа", modes: ["ops"] },
  { id: "an", num: "07", title: "Аналитика", Component: Analytics, group: "Аналитика" },
  // ЭКРАН «ОТЧЁТЫ» (был 08) УДАЛЁН 19.08.2026. Он спрашивал у модели цифры
  // словами — ровно то, что теперь делает строка «Спросите Soro о бизнесе»
  // на экране 08, причём с основаниями и источниками. Два входа в одну
  // функцию на встрече только путали: банк спрашивал, чем они отличаются.
  //
  // Бэкенд `/api/reports/ask` НЕ ТРОНУТ: он живой, отвечает и покрыт
  // тестами. Понадобится вернуть экран — он собирается обратно из истории.
  { id: "ex", num: "08", title: "Руководству", Component: Executive, group: "Аналитика" },
  { id: "ai", num: "09", title: "Отчёты AI", Component: AIReports, group: "Аналитика" },
  { id: "sc", num: "10", title: "Безопасность", Component: Security, group: "Доверие" },
  { id: "demo", num: "11", title: "Демо", Component: KillerDemo, group: "Доверие", accent: true },
];

/** Экраны текущего режима, в порядке объявления. */
function visibleScreens(mode: Mode): Screen[] {
  return SCREENS.filter((screen) => !screen.modes || screen.modes.includes(mode));
}

function Topbar({
  name,
  mode,
  onMode,
}: {
  name: string;
  mode: Mode;
  onMode: (next: Mode) => void;
}) {
  const { lang, setLang, t } = useLang();
  return (
    <div className="topbar">
      <div className="logo">
        <svg className="mark" viewBox="0 0 24 24" fill="none">
          <rect x="2" y="9" width="6" height="6" rx="1.6" fill="#E8506B" />
          <rect x="9" y="2" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
          <rect x="9" y="16" width="6" height="6" rx="1.6" fill="#E8506B" opacity=".6" />
          <rect x="16" y="9" width="6" height="6" rx="1.6" fill="#DCA84C" />
        </svg>
        <div>
          <div className="word">Soro Business</div>
          <small>zehnlab · console</small>
        </div>
      </div>

      <WorkspacePicker name={name} />

      {/* Кому показываем стенд. Переключатель убирает из меню технические
          экраны — руководителю незачем видеть площадку и каналы, а лишние
          пункты на встрече превращаются в вопросы не по делу. */}
      <div className="modetog" role="group" aria-label="Режим просмотра">
        <button className={mode === "ops" ? "on" : ""} onClick={() => onMode("ops")}>
          {t("Операции")}
        </button>
        <button className={mode === "exec" ? "on" : ""} onClick={() => onMode("exec")}>
          {t("Руководству")}
        </button>
      </div>

      <div className="spacer" />
      <div className="demoflag">{t("демо-стенд")}</div>
      <div className="langtog">
        <button className={lang === "ru" ? "on" : ""} onClick={() => setLang("ru")}>
          RU
        </button>
        <button className={lang === "tj" ? "on" : ""} onClick={() => setLang("tj")}>
          TJ
        </button>
      </div>
    </div>
  );
}

// Кнопка воркспейса в эталоне только нарисована: банк один и зашит в
// разметку. Здесь она открывает список банков стенда и форму «Добавить
// банк» — раздел 1.1 обещает изолированное пространство на каждый банк, и
// заводить его вручную в базе больше не нужно.
function WorkspacePicker({ name }: { name: string }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<WorkspaceRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || rows !== null) return;
    listWorkspaces()
      .then(setRows)
      .catch(() => setError("Не удалось получить список банков"));
  }, [open, rows]);

  function pick(next: string) {
    selectWorkspace(next);
    // Перезагрузка, а не перерисовка: воркспейс меняет ВСЁ — документы,
    // диалоги, аналитику, каналы. Обновлять состояние семи экранов по
    // одному значит однажды забыть про восьмой и показать чужие данные.
    window.location.reload();
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    try {
      const created = await addWorkspace(slug.trim(), title.trim());
      pick(created.slug);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Такой банк уже есть"
          : "Проверьте slug: латиница, цифры и дефис",
      );
    }
  }

  return (
    <div className="wspick">
      <button className="ws" onClick={() => setOpen(!open)}>
        <span className="dot" />
        <span className="lbl">{t("воркспейс")}</span> {name} ▾
      </button>

      {open && (
        <div className="wsmenu">
          {rows === null && <div className="wsempty">…</div>}
          {rows?.map((row) => (
            <button
              key={row.slug}
              className={row.slug === currentWorkspace() ? "wsrow on" : "wsrow"}
              onClick={() => pick(row.slug)}
            >
              <b>{row.name}</b>
              <small>
                {row.slug} · {row.documents} док. · {row.conversations} диал.
              </small>
            </button>
          ))}

          {adding ? (
            <form className="wsadd" onSubmit={add}>
              <input
                className="text"
                placeholder={t("Название банка")}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                autoFocus
              />
              <input
                className="text mono"
                placeholder="slug: bank-demo"
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
              />
              <div className="wsactions">
                <button className="btn primary" type="submit">
                  {t("Добавить")}
                </button>
                <button
                  className="btn"
                  type="button"
                  onClick={() => setAdding(false)}
                >
                  {t("Отмена")}
                </button>
              </div>
            </form>
          ) : (
            <button className="wsrow add" onClick={() => setAdding(true)}>
              + {t("Добавить банк")}
            </button>
          )}

          {error && <div className="wsempty fail">{error}</div>}
        </div>
      )}
    </div>
  );
}

// ВХОДА НЕТ. Раньше первым экраном была форма пароля, но проверять его
// было нечем: `api/auth.py` — стаб, и форма всё равно пускала внутрь по
// таймауту, объясняя это словами «бэкенд недоступен». То есть замок был
// нарисован. На демо-стенде он не нужен и мешает: консоль открывают на
// встрече с проектора, лишний шаг — лишние тридцать секунд и риск
// забытого пароля.
//
// Если консоль когда-нибудь выйдет за пределы стенда, вход вернётся
// вместе с настоящей проверкой на бэкенде (раздел 9 ТЗ), а не отдельно
// от неё.
export default function App() {
  const [current, setCurrent] = useState<string>("ov");
  const [mode, setMode] = useState<Mode>("ops");
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);

  // Достучались ли до бэкенда. Отдельно от `info`, потому что `null` там
  // бывает и в первую секунду загрузки — а плашку «сервер не отвечает»
  // показывать в этот момент нельзя, она мигала бы при каждом переходе.
  const [offline, setOffline] = useState(false);

  // Подпись в футере навигации должна отражать факт, а не эталон: там
  // зашиты «Soro-27B · FP8» и «Аудит-лог включён», а на сервере GPTQ-int4,
  // и аудит теперь выключается переключателем на экране 01.
  const loadInfo = useCallback(() => {
    getWorkspace()
      .then((data) => {
        setInfo(data);
        setOffline(false);
      })
      .catch(() => {
        setInfo(null);
        setOffline(true);
      });
  }, []);

  useEffect(() => {
    loadInfo();
  }, [current, loadInfo]);

  // Смена режима не должна оставлять пользователя на экране, которого в
  // новом режиме нет: иначе главная область пустеет, а меню показывает,
  // что ничего не выбрано.
  function switchMode(next: Mode) {
    setMode(next);
    const allowed = visibleScreens(next);
    if (!allowed.some((screen) => screen.id === current)) {
      // Руководителю открываем его дашборд, а не первый попавшийся экран.
      setCurrent(next === "exec" ? "ex" : "ov");
    }
  }

  return (
    <div className="shell">
      <Topbar name={info?.name ?? "…"} mode={mode} onMode={switchMode} />
      <div className="body">
        <Nav current={current} onPick={setCurrent} info={info} mode={mode} />

        <main>
          {/* Плашка стоит НАД экраном, а не вместо него: экран «Демо»
              работает без сервера, и подменять его сообщением об ошибке
              значило бы отнять у презентатора единственное, что осталось
              рабочим, когда стенд недоступен. */}
          {offline && <Offline onRetry={loadInfo} />}

          {visibleScreens(mode).map(({ id, Component }) => (
            <section key={id} className={`screen ${id === current ? "on" : ""}`} id={id}>
              {/* onOpen позволяет экрану увести на другой: с дашборда — в
                  отчёты, из финала демо — на дашборд. Без него финальная
                  кнопка демо была бы нарисованной. */}
              {id === current && <Component onOpen={setCurrent} />}
            </section>
          ))}
        </main>
      </div>
    </div>
  );
}

function Nav({
  current,
  onPick,
  info,
  mode,
}: {
  current: string;
  onPick: (id: string) => void;
  info: WorkspaceInfo | null;
  mode: Mode;
}) {
  const { t } = useLang();
  const [waiting, setWaiting] = useState(0);

  // Бейдж «ждут оператора» в меню. Эскалация случается, пока оператор
  // смотрит другой экран, и узнать о ней он должен не открыв инбокс, а
  // до того. Сокет здесь заводить не стали: в инбоксе он уже есть, а
  // меню достаточно опроса раз в пятнадцать секунд.
  useEffect(() => {
    let alive = true;
    const tick = () =>
      inboxCounters()
        .then((counters) => alive && setWaiting(counters.waiting))
        .catch(() => alive && setWaiting(0));
    tick();
    const timer = window.setInterval(tick, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const screens = visibleScreens(mode);

  return (
        <nav>
          <div className="navlbl">{t("воркспейс")}</div>
          {screens.map((s, index) => {
            // Заголовок группы печатаем перед первым её экраном: держать
            // отдельный массив групп значит однажды забыть в нём новый
            // экран и потерять его из меню.
            const newGroup = index === 0 || screens[index - 1].group !== s.group;
            return (
              <Fragment key={s.id}>
                {newGroup && <div className="navgroup">{t(s.group)}</div>}
                <button
                  className={[
                    s.id === current ? "on" : "",
                    s.accent ? "accent" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => onPick(s.id)}
                >
                  <span className="n">{s.num}</span>
                  {t(s.title)}
                  {s.id === "ib" && waiting > 0 && (
                    <span className="navbadge">{waiting}</span>
                  )}
                </button>
              </Fragment>
            );
          })}
          <div className="navfoot">
            {t("Модель")} <b>{info ? info.model.split("/").pop() : "…"}</b>
            <br />
            {t("Хостинг")} <b>{t("Душанбе, on-prem")}</b>
            <br />
            {t("Аудит-лог")}{" "}
            <b>
              {info?.security.audit_log === false ? t("выключен") : t("включён")}
            </b>
            {/* «Аптайм 30 дн 99,94%» убран 19.08.2026. Число пришло из
                эталона и было просто нарисовано: мониторинга доступности в
                системе нет, считать аптайм не из чего. На встрече по нему
                задают вопрос «за какой период и чем меряли» — и ответить
                нечем. Вернём, когда появится реальная метрика. */}
          </div>
        </nav>
  );
}
