import { useState } from "react";
import Login from "./screens/Login";
import Overview from "./screens/Overview";
import Knowledge from "./screens/Knowledge";
import Playground from "./screens/Playground";
import Omni from "./screens/Omni";
import Channels from "./screens/Channels";
import Inbox from "./screens/Inbox";
import Analytics from "./screens/Analytics";

// Идентификаторы экранов совпадают с прототипом (data-go в
// soro-business-console-2.html) — так проще сверять реализацию с эталоном.
const SCREENS = [
  { id: "ov", num: "01", title: "Обзор", Component: Overview },
  { id: "kb", num: "02", title: "База знаний", Component: Knowledge },
  { id: "pg", num: "03", title: "Площадка", Component: Playground },
  { id: "om", num: "04", title: "Омниканальность", Component: Omni },
  { id: "ch", num: "05", title: "Каналы", Component: Channels },
  { id: "ib", num: "06", title: "Инбокс", Component: Inbox },
  { id: "an", num: "07", title: "Аналитика", Component: Analytics },
] as const;

export default function App() {
  const [authorized, setAuthorized] = useState(false);
  const [current, setCurrent] = useState<string>("ov");

  if (!authorized) {
    return <Login onDone={() => setAuthorized(true)} />;
  }

  const screen = SCREENS.find((s) => s.id === current) ?? SCREENS[0];
  const Current = screen.Component;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          Soro <em>Business</em>
        </div>
        <nav className="nav">
          {SCREENS.map((s) => (
            <button
              key={s.id}
              className={s.id === current ? "on" : ""}
              onClick={() => setCurrent(s.id)}
            >
              <span className="num">{s.num}</span>
              {s.title}
            </button>
          ))}
        </nav>
      </aside>
      <main className="work">
        <Current />
      </main>
    </div>
  );
}
