// Плашка «бэкенд недоступен» с полем для адреса.
//
// ЗАЧЕМ. Консоль может быть выложена отдельно от бэкенда — на GitHub
// Pages, — а бэкенд живёт на машине команды за туннелем. Если адрес не
// передан (открыли ссылку без `?api=`) или туннель лёг, все экраны просто
// висят с многоточиями: ни ошибки, ни подсказки. На встрече это читается
// как «продукт не работает», хотя чаще всего дело в ссылке.
//
// Плашка отвечает на три вопроса сразу: что не так, почему и что нажать.
// Поле для адреса здесь же — чтобы поправить прямо на встрече, не
// пересобирая сайт и не выпрашивая новую ссылку.

import { useState } from "react";
import { apiOrigin, setApiOrigin } from "../lib/api";

export default function Offline({ onRetry }: { onRetry: () => void }) {
  const current = apiOrigin();
  const [value, setValue] = useState(current);

  // Разные причины — разные слова. Пустой адрес значит, что консоль
  // выложена отдельно и ссылку открыли без параметра; заполненный — что
  // адрес есть, но не отвечает.
  const missing = current === "";

  return (
    <div className="offline">
      <div className="offhead">
        <span className="offdot" />
        {missing ? "Не указан адрес сервера" : "Сервер не отвечает"}
      </div>

      <p className="offtext">
        {missing ? (
          <>
            Консоль открыта отдельно от сервера, и адрес не передан. Откройте
            ссылку с адресом в конце — вида{" "}
            <code>?api=https://…</code> — или введите его здесь.
          </>
        ) : (
          <>
            Консоль обращается к <code>{current}</code>, но ответа нет.
            Обычно это значит, что стенд выключен или туннель перезапущен и
            выдал новый адрес.
          </>
        )}
      </p>

      <form
        className="offrow"
        onSubmit={(event) => {
          event.preventDefault();
          setApiOrigin(value);
        }}
      >
        <input
          className="text"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="https://адрес-стенда"
          aria-label="Адрес сервера"
        />
        <button className="btn primary" type="submit" disabled={!value.trim()}>
          Подключиться
        </button>
        <button className="btn" type="button" onClick={onRetry}>
          Повторить
        </button>
      </form>

      <div className="offnote">
        Экран «Демо» работает без сервера — его можно показывать и сейчас.
      </div>
    </div>
  );
}
