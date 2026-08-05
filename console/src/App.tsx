import { useState } from "react";
import Login from "./screens/Login";
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

function Topbar() {
  const [lang, setLang] = useState<"ru" | "tj">("ru");
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
      <button className="ws">
        <span className="dot" />
        <span className="lbl">воркспейс</span> Банк Эсхата ▾
      </button>
      <div className="spacer" />
      <div className="demoflag">демо-стенд</div>
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

export default function App() {
  const [authorized, setAuthorized] = useState(false);
  const [current, setCurrent] = useState<string>("ov");

  if (!authorized) return <Login onDone={() => setAuthorized(true)} />;

  return (
    <div className="shell">
      <Topbar />
      <div className="body">
        <nav>
          <div className="navlbl">Воркспейс</div>
          {SCREENS.map((s) => (
            <button
              key={s.id}
              className={s.id === current ? "on" : ""}
              onClick={() => setCurrent(s.id)}
            >
              <span className="n">{s.num}</span>
              {s.title}
            </button>
          ))}
          <div className="navfoot">
            Модель <b>Soro-27B · FP8</b>
            <br />
            Хостинг <b>Душанбе, on-prem</b>
            <br />
            Аудит-лог <b>включён</b>
            <br />
            Аптайм 30 дн <b>99,94%</b>
          </div>
        </nav>

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
