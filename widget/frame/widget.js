// Логика чата внутри iframe (раздел 7.2 ТЗ).
//
// Три вещи, ради которых он написан руками, а не собран Vite:
//   1. виджет отдаётся бэкендом как есть, без сборки — на демо правка
//      видна после F5, а не после `npm run build`;
//   2. зависимостей нет, значит нечему устареть в шаблоне сайта банка;
//   3. код читается целиком, и ИТ-служба банка может его прочитать перед
//      тем, как разрешить вставку к себе на страницу.
//
// ПОТОК ОДИН НА КЛИЕНТА, А НЕ НА ВОПРОС. EventSource открывается при
// загрузке и живёт, пока открыт виджет: в него приходит история при
// подключении, ответы бота и реплики оператора. Поэтому отправка вопроса —
// обычный POST, который сразу возвращает 202.
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var ws = params.get("ws") || "";

  // Идентичность клиента. Ключ и способ — из раздела 7.2. Хранится в
  // localStorage САМОГО ВИДЖЕТА, то есть на нашем домене: сайт банка его
  // не видит, а клиент, вернувшийся через неделю, узнаётся.
  var uid = localStorage.getItem("soro_uid");
  if (!uid) {
    uid = crypto.randomUUID();
    localStorage.setItem("soro_uid", uid);
  }

  var log = document.getElementById("log");
  var input = document.getElementById("text");
  var send = document.getElementById("send");

  // Пузырь, в который сейчас капает ответ модели. Живёт от первой delta
  // до final, потом обнуляется.
  var streaming = null;
  var typing = null;

  function atBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  }

  function scroll(force) {
    if (force || atBottom()) log.scrollTop = log.scrollHeight;
  }

  function bubble(role, text) {
    var node = document.createElement("div");
    node.className = "m " + role;
    if (role === "operator") {
      var from = document.createElement("span");
      from.className = "from";
      from.textContent = "Оператор";
      node.appendChild(from);
    }
    node.appendChild(document.createTextNode(text || ""));
    log.appendChild(node);
    scroll(role === "user");
    return node;
  }

  // Текст ставим через textContent, а не innerHTML: он приходит от модели
  // и из инбокса оператора, и вставлять его как разметку нельзя. Ссылки
  // [1] остаются в тексте как есть — под ответом их дублируют бейджи.
  function setText(node, text) {
    var from = node.querySelector(".from");
    node.textContent = "";
    if (from) node.appendChild(from);
    node.appendChild(document.createTextNode(text));
  }

  function sources(node, list) {
    if (!list || !list.length) return;
    var box = document.createElement("div");
    box.className = "src";
    list.forEach(function (source) {
      var label = "[" + source.n + "] " + (source.title || "документ");
      var hint = source.page ? source.title + ", стр. " + source.page : source.title;
      var item;
      if (source.source_url) {
        item = document.createElement("a");
        item.href = source.source_url;
        item.target = "_blank";
        item.rel = "noopener";
      } else {
        item = document.createElement("span");
      }
      item.textContent = label;
      item.title = hint || "";
      box.appendChild(item);
    });
    node.appendChild(box);
    scroll();
  }

  function showTyping() {
    if (typing) return;
    typing = document.createElement("div");
    typing.className = "m bot dots";
    typing.innerHTML = "<span></span><span></span><span></span>";
    log.appendChild(typing);
    scroll(true);
  }

  function hideTyping() {
    if (typing) log.removeChild(typing);
    typing = null;
  }

  // --- поток событий -------------------------------------------------------

  var source = new EventSource(
    "/widget/stream?uid=" + encodeURIComponent(uid) + "&ws=" + encodeURIComponent(ws)
  );

  source.addEventListener("history", function (event) {
    var messages = JSON.parse(event.data).messages || [];
    log.textContent = "";
    messages.forEach(function (message) {
      bubble(message.role === "user" ? "user" : message.role === "operator" ? "operator" : "bot", message.text);
    });
    if (!messages.length) {
      bubble("system", "Салом! Саволатонро нависед — задайте вопрос.");
    }
    scroll(true);
  });

  source.addEventListener("delta", function (event) {
    hideTyping();
    var piece = JSON.parse(event.data).text || "";
    if (!streaming) streaming = bubble("bot", "");
    streaming.appendChild(document.createTextNode(piece));
    scroll();
  });

  source.addEventListener("final", function (event) {
    hideTyping();
    var data = JSON.parse(event.data);
    // `final` ЗАМЕНЯЕТ показанное: ядро могло подменить ответ фразой об
    // эскалации уже после того, как куски уехали в поток. Оставить на
    // экране прежний текст значит показать клиенту то, что мы решили не
    // отправлять.
    if (data.text) {
      var node = streaming || bubble("bot", "");
      setText(node, data.text);
      sources(node, data.sources);
    } else if (streaming) {
      log.removeChild(streaming);
    }
    streaming = null;
    send.disabled = false;
  });

  source.addEventListener("escalated", function () {
    bubble("system", "Передаю специалисту — он видит нашу переписку.");
  });

  source.addEventListener("operator_msg", function (event) {
    hideTyping();
    bubble("operator", JSON.parse(event.data).text || "");
  });

  source.addEventListener("error", function (event) {
    // Событие error приходит и от нашего бэкенда (с данными), и от самого
    // EventSource при обрыве связи (без данных). Второе он чинит сам,
    // переподключаясь, — писать о нём клиенту незачем.
    if (!event.data) return;
    hideTyping();
    bubble("system", JSON.parse(event.data).message || "Что-то пошло не так");
    send.disabled = false;
  });

  // --- отправка ------------------------------------------------------------

  function ask() {
    var text = input.value.trim();
    if (!text) return;

    bubble("user", text);
    input.value = "";
    input.style.height = "auto";
    send.disabled = true;
    showTyping();

    fetch("/widget/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid: uid, ws: ws, text: text }),
    }).catch(function () {
      hideTyping();
      bubble("system", "Не удалось отправить — проверьте связь");
      send.disabled = false;
    });
  }

  send.onclick = ask;
  input.addEventListener("keydown", function (event) {
    // Enter отправляет, Shift+Enter переносит строку — как в мессенджерах.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask();
    }
  });
  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 96) + "px";
  });

  // --- «Продолжить в Telegram» --------------------------------------------

  document.getElementById("tg").onclick = function () {
    fetch("/widget/link-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid: uid, ws: ws }),
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        // Открываем в новой вкладке: iframe чужого сайта не должен
        // уводить страницу банка со страницы банка.
        window.open(data.url, "_blank", "noopener");
      })
      .catch(function () {
        bubble("system", "Ссылка не выдалась — попробуйте ещё раз");
      });
  };

  document.getElementById("close").onclick = function () {
    parent.postMessage({ type: "soro:close" }, "*");
  };
})();
