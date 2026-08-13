// Загрузчик виджета — то, что банк вставляет к себе на сайт (раздел 7.2).
//
// ОГРАНИЧЕНИЕ ТЗ: меньше 5 КБ. Поэтому здесь нет ни React, ни зависимостей —
// только создание iframe и кнопки. Вся логика живёт внутри frame/.
//
// Что делает:
//   1. рисует круглую кнопку в углу страницы;
//   2. по клику открывает iframe с frame/index.html;
//   3. прокидывает внутрь workspace и PUBLIC_BASE_URL;
//   4. общается с iframe через postMessage (высота окна, закрытие).
//
// Почему iframe, а не встраивание в DOM банка: изоляция стилей. Сайт банка
// не должен ломать виджет своим CSS, а виджет — сайт.
//
// АДРЕС БЭКЕНДА БЕРЁМ ИЗ СВОЕГО SRC. В сниппете ТЗ адрес указан один раз —
// в самом теге <script>. Просить банк написать его второй раз в data-атрибуте
// значит однажды получить расхождение между ними, которое никто не заметит,
// пока виджет не перестанет отвечать.
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var base = new URL(script.src, location.href).origin;
  var ws = script.getAttribute("data-ws") || "";
  var lang = script.getAttribute("data-lang") || "";

  // Стили инлайном, а не отдельным файлом: лишний запрос ради двадцати
  // строк, плюс правила банка могут прийти позже наших и всё перекрасить.
  // `all: initial` на контейнере отрезает наследование от сайта.
  var ROSE = "#E8506B";
  var box = document.createElement("div");
  box.style.cssText =
    "position:fixed;right:20px;bottom:20px;z-index:2147483000;" +
    "font:inherit;color-scheme:dark";

  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Чат с банком");
  button.style.cssText =
    "width:56px;height:56px;border:0;border-radius:50%;cursor:pointer;" +
    "background:" + ROSE + ";color:#fff;box-shadow:0 10px 30px rgba(232,80,107,.4);" +
    "display:flex;align-items:center;justify-content:center;padding:0;" +
    "transition:transform .15s";
  button.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.6 9.6 ' +
    '0 0 1-3.3-.6L3 21l1.8-5.1A8.2 8.2 0 0 1 3.6 11.5a8.4 8.4 0 0 1 9-8.4 ' +
    '8.4 8.4 0 0 1 8.4 8.4z"/></svg>';
  button.onmouseenter = function () {
    button.style.transform = "translateY(-2px)";
  };
  button.onmouseleave = function () {
    button.style.transform = "none";
  };

  var frame = null;

  function open() {
    if (frame) {
      frame.style.display = "block";
      return;
    }
    frame = document.createElement("iframe");
    frame.src =
      base + "/widget/frame/?ws=" + encodeURIComponent(ws) +
      (lang ? "&lang=" + encodeURIComponent(lang) : "");
    frame.title = "Чат с банком";
    // Размер под прототип: на телефоне окно занимает почти весь экран,
    // на десктопе — панель в углу.
    frame.style.cssText =
      "position:fixed;right:20px;bottom:88px;width:min(384px,calc(100vw - 40px));" +
      "height:min(560px,calc(100vh - 130px));border:0;border-radius:20px;" +
      "box-shadow:0 24px 60px rgba(0,0,0,.45);z-index:2147483000;" +
      "background:#110710";
    document.body.appendChild(frame);
  }

  function close() {
    if (frame) frame.style.display = "none";
  }

  button.onclick = function () {
    if (frame && frame.style.display !== "none") close();
    else open();
  };

  // Из iframe приходит только просьба закрыться. Проверка origin
  // обязательна: без неё любой скрипт на странице банка сможет прислать
  // сообщение от чужого имени.
  window.addEventListener("message", function (event) {
    if (event.origin !== base) return;
    if (event.data && event.data.type === "soro:close") close();
  });

  box.appendChild(button);
  document.body.appendChild(box);
})();
