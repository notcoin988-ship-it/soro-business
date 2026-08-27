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

  // Название компании в шапке. В разметке стоит запасное значение, но
  // виджет обслуживает несколько заказчиков сразу, и зашитое имя показывало
  // чужое: клиент страховой видел в шапке банк. Спрашиваем настоящее по
  // тому же `ws`, с которым работаем.
  var who = document.querySelector(".who");
  if (who && ws) {
    fetch("/api/workspace?ws=" + encodeURIComponent(ws), {
      headers: { "ngrok-skip-browser-warning": "1" },
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data || !data.name) return;
        // Подпись под именем оставляем — меняем только сам заголовок.
        var note = who.querySelector("small");
        who.textContent = data.name;
        if (note) who.appendChild(note);
      })
      .catch(function () {
        // Имя — украшение шапки: не ответило, остаётся запасное из разметки.
      });
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
    if (!messages.length) showEmpty();
    scroll(true);
  });

  // Пустое состояние вместо пустой ленты. Человек, открывший виджет
  // впервые, не знает, что у бота спрашивать, — и чаще всего закрывает
  // его, не написав ничего. Две подсказки-кнопки снимают этот вопрос;
  // текст у них короткий, потому что это пример, а не сценарий.
  function showEmpty() {
    var box = document.createElement("div");
    box.className = "empty";

    var title = document.createElement("h2");
    title.textContent = "Салом! Чем помочь?";
    var hint = document.createElement("p");
    hint.textContent =
      "Отвечаю по документам банка на таджикском и русском. " +
      "Если вопрос про ваш счёт — соединю со специалистом.";
    box.appendChild(title);
    box.appendChild(hint);

    var asks = document.createElement("div");
    asks.className = "asks";
    ["Мӯҳлати пасандози мӯҳлатнок чанд рӯз аст?", "Что такое кредит «Многоцелевой»?"]
      .forEach(function (question) {
        var button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.onclick = function () {
          input.value = question;
          ask();
        };
        asks.appendChild(button);
      });
    box.appendChild(asks);
    log.appendChild(box);
  }

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

  // Диалог закрыт оператором — просим оценить его работу. Один раз и
  // только здесь: пока разговор идёт, спрашивать «как вам оператор» рано.
  source.addEventListener("closed", function (event) {
    var rateFor = JSON.parse(event.data).rate_for;
    if (!rateFor) return;
    askRating(rateFor);
  });

  function askRating(messageId) {
    var box = document.createElement("div");
    box.className = "m system rate";
    box.appendChild(document.createTextNode("Как вам работа специалиста?"));

    var row = document.createElement("div");
    row.className = "raterow";

    // Пять звёзд: при наведении подсвечиваются все до текущей — так
    // человек видит, что ставит «четыре», а не «четвёртую».
    [1, 2, 3, 4, 5].forEach(function (score) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "star";
      button.textContent = "★";
      button.title = score + " из 5";
      button.onmouseenter = function () {
        Array.prototype.forEach.call(row.children, function (star, index) {
          star.classList.toggle("lit", index < score);
        });
      };
      button.onclick = function () {
        // Кнопки убираем сразу: оценка ставится один раз, и второй клик
        // по той же звезде не должен выглядеть как «не засчиталось».
        row.textContent = "";
        box.appendChild(document.createTextNode(" Спасибо, " + score + " из 5!"));
        fetch("/widget/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            uid: uid,
            ws: ws,
            message_id: messageId,
            score: score,
          }),
        }).catch(function () {
          box.appendChild(document.createTextNode(" (не отправилось)"));
        });
      };
      row.appendChild(button);
    });

    // Подсветка гаснет, когда мышь ушла со всего ряда: иначе на экране
    // остаётся «четыре звезды», которых человек не ставил.
    row.onmouseleave = function () {
      Array.prototype.forEach.call(row.children, function (star) {
        star.classList.remove("lit");
      });
    };

    box.appendChild(row);
    log.appendChild(box);
    scroll(true);
  }

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

    // Пустое состояние с подсказками уступает место разговору.
    var empty = log.querySelector(".empty");
    if (empty) log.removeChild(empty);

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
