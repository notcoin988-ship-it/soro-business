import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// порядок важен: сначала эталон, потом наши добавления
import "./prototype.css";
import "./theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
