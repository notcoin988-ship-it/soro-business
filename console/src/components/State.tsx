import { ReactNode } from "react";

// Три состояния, которые есть у любого экрана с данными: «читаю»,
// «пусто» и «сломалось». В прототипе их нет вовсе — там всё нарисовано и
// всегда полное, — и первые версии экранов повторяли эту ошибку каждый
// по-своему: где-то пустой блок, где-то красная строка без выхода.
//
// ПОЧЕМУ «ЧИТАЮ» И «ПУСТО» — РАЗНЫЕ СОСТОЯНИЯ. Массив, который ещё не
// пришёл с бэкенда, и массив, которого нет, в React выглядят одинаково:
// оба пустые. Экран 02 из-за этого встречал оператора надписью «Пока
// пусто. Загрузите тарифы банка» — при полной базе знаний, просто пока
// летел запрос. Отсюда правило: данные до загрузки это `null`, а не `[]`.
//
// ПОЧЕМУ У ОШИБКИ ЕСТЬ КНОПКА. Без неё единственный выход — F5, а он
// сбрасывает и то, что оператор успел набрать в поле ответа.

export function Loading({ text = "Читаю…" }: { text?: string }) {
  return <div className="state loading">{text}</div>;
}

export function Empty({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="state empty">
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {action}
    </div>
  );
}

export function Failed({
  text = "Бэкенд не отвечает",
  onRetry,
}: {
  text?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state failed">
      <span>{text}</span>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}
