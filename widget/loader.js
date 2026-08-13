// Загрузчик виджета — то, что банк вставляет к себе на сайт (раздел 7.2):
// кнопка в углу и iframe с frame/index.html. Вся логика чата — там.
//
// ЛИМИТ ТЗ: меньше 5 КБ — отсюда ни зависимостей, ни длинных пояснений;
// они в widget/README.md, а размер стережёт тест.
//
// iframe, а не DOM банка: изоляция стилей в обе стороны. Адрес бэкенда
// берём из своего src — в сниппете он написан один раз.
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var base = new URL(script.src, location.href).origin;
  var ws = script.getAttribute("data-ws") || "";
  var lang = script.getAttribute("data-lang") || "";

  // Стили инлайном: лишний запрос ради двадцати строк, а правила банка
  // могут прийти позже наших и всё перекрасить.
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
    'stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">' +
    '<path d="M4 5h16v11H9l-5 4z"/></svg>';
  button.onmouseenter = function () {
    button.style.transform = "translateY(-2px)";
  };
  button.onmouseleave = function () {
    button.style.transform = "none";
  };

  var frame = null;

  // Порог по ШИРИНЕ ОКНА, а не «мобильный ли это»: на планшете в портрете
  // панель тоже не помещается. Медиазапрос не годится — стили iframe живут
  // на странице банка, а <style> туда загрузчик не добавляет.
  function narrow() {
    return window.innerWidth < 560;
  }

  function frameStyle() {
    if (narrow()) {
      // 100dvh перед 100vh: на мобильном Safari адресная строка съезжает,
      // и окно на 100vh уходит под неё нижним краем — вместе с полем ввода.
      return (
        "position:fixed;inset:0;width:100%;height:100dvh;height:100vh;" +
        "border:0;border-radius:0;z-index:2147483000;background:#110710"
      );
    }
    return (
      "position:fixed;right:20px;bottom:88px;width:min(384px,calc(100vw - 40px));" +
      "height:min(560px,calc(100vh - 130px));border:0;border-radius:20px;" +
      "box-shadow:0 24px 60px rgba(0,0,0,.45);z-index:2147483000;" +
      "background:#110710"
    );
  }

  // Поворот телефона и ресайз окна — один и тот же случай.
  window.addEventListener("resize", function () {
    var shown = frame && frame.style.display !== "none";
    if (shown) frame.style.cssText = frameStyle();
    box.style.display = shown && narrow() ? "none" : "block";
  });

  function open() {
    if (frame) {
      frame.style.cssText = frameStyle();
      frame.style.display = "block";
    } else {
      frame = document.createElement("iframe");
      frame.src =
        base + "/widget/frame/?ws=" + encodeURIComponent(ws) +
        (lang ? "&lang=" + encodeURIComponent(lang) : "");
      frame.title = "Чат с банком";
      frame.style.cssText = frameStyle();
      document.body.appendChild(frame);
    }
    // На узком экране кнопка перекрыла бы ленту; закрыть чат есть чем —
    // крестиком в шапке окна.
    if (narrow()) box.style.display = "none";
  }

  function close() {
    if (frame) frame.style.display = "none";
    box.style.display = "block";
  }

  button.onclick = function () {
    if (frame && frame.style.display !== "none") close();
    else open();
  };

  // Из iframe приходит только просьба закрыться. Проверка origin
  // обязательна: иначе прислать её сможет любой скрипт на странице банка.
  window.addEventListener("message", function (event) {
    if (event.origin !== base) return;
    if (event.data && event.data.type === "soro:close") close();
  });

  box.appendChild(button);
  document.body.appendChild(box);
})();
