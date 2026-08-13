import { useEffect, useState } from "react";
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

// Каркас консоли повторяет прототип: topbar, боковая навигация с номерами
// экранов и футером параметров, справа — экран. Идентификаторы те же, что
// data-go в soro-business-console-2.html.
const SCREENS = [
  { id: "ov", num: "01", title: "Обзор", Component: Overview },
  { id: "kb", num: "02", title: "База знаний", Component: Knowledge },
  { id: "pg", num: "03", title: "Площадка", Component: Playground },
  { id: "om", num: "04", title: "Омниканальность", Component: Omni },
  { id: "ch", num: "05", title: "Каналы", Component: Channels },
  { id: "ib", num: "06", title: "Инбокс оператора", Component: Inbox },
  { id: "an", num: "07", title: "Аналитика", Component: Analytics },
] as const;

function Topbar({ name }: { name: string }) {
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
  const [info, setInfo] = useState<WorkspaceInfo | null>(null);

  // Подпись в футере навигации должна отражать факт, а не эталон: там
  // зашиты «Soro-27B · FP8» и «Аудит-лог включён», а на сервере GPTQ-int4,
  // и аудит теперь выключается переключателем на экране 01.
  useEffect(() => {
    getWorkspace()
      .then(setInfo)
      .catch(() => setInfo(null));
  }, [current]);

  return (
    <div className="shell">
      <Topbar name={info?.name ?? "…"} />
      <div className="body">
        <Nav current={current} onPick={setCurrent} info={info} />

        <main>
          {SCREENS.map(({ id, Component }) => (
            <section key={id} className={`screen ${id === current ? "on" : ""}`} id={id}>
              {id === current && <Component />}
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
}: {
  current: string;
  onPick: (id: string) => void;
  info: WorkspaceInfo | null;
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

  return (
        <nav>
          <div className="navlbl">{t("воркспейс")}</div>
          {SCREENS.map((s) => (
            <button
              key={s.id}
              className={s.id === current ? "on" : ""}
              onClick={() => onPick(s.id)}
            >
              <span className="n">{s.num}</span>
              {t(s.title)}
              {s.id === "ib" && waiting > 0 && (
                <span className="navbadge">{waiting}</span>
              )}
            </button>
          ))}
          <div className="navfoot">
            {t("Модель")} <b>{info ? info.model.split("/").pop() : "…"}</b>
            <br />
            {t("Хостинг")} <b>{t("Душанбе, on-prem")}</b>
            <br />
            {t("Аудит-лог")}{" "}
            <b>
              {info?.security.audit_log === false ? t("выключен") : t("включён")}
            </b>
            <br />
            {t("Аптайм 30 дн")} <b>99,94%</b>
          </div>
        </nav>
  );
}
