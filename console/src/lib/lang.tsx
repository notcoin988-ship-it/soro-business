import { ReactNode, createContext, useContext, useEffect, useState } from "react";
import { Lang, translate } from "./i18n";

// Язык консоли живёт в контексте, а не в пропсах: подписи нужны и в
// шапке, и в навигации, и внутри экранов, а протаскивать язык через семь
// уровней компонентов — верный способ где-нибудь его потерять.
//
// Выбор запоминается: оператор, работающий на таджикском, не должен
// переключать язык после каждой перезагрузки.

const KEY = "soro_lang";

interface Ctx {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (text: string) => string;
}

const LangContext = createContext<Ctx>({
  lang: "ru",
  setLang: () => {},
  t: (text) => text,
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(
    () => (localStorage.getItem(KEY) as Lang) || "ru",
  );

  useEffect(() => {
    localStorage.setItem(KEY, lang);
    // Атрибут нужен не только для порядка: браузер по нему переносит
    // слова и выбирает шрифтовые начертания.
    document.documentElement.lang = lang === "tj" ? "tg" : "ru";
  }, [lang]);

  return (
    <LangContext.Provider
      value={{ lang, setLang, t: (text) => translate(text, lang) }}
    >
      {children}
    </LangContext.Provider>
  );
}

export function useLang(): Ctx {
  return useContext(LangContext);
}
