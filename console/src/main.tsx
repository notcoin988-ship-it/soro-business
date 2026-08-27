import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { LangProvider } from "./lib/lang";
// порядок важен: сначала эталон, потом наши добавления
import "./prototype.css";
import "./theme.css";
// модули AI-аналитики: новые экраны, которых в прототипе не было
import "./enterprise.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LangProvider>
      <App />
    </LangProvider>
  </React.StrictMode>
);
