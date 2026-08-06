import { useEffect, useRef } from "react";

// Подтверждение необратимого действия. В эталоне диалога нет — там ничего
// не удаляется, — поэтому собран из тех же кирпичей: карточка .card,
// заголовок шрифтом Unbounded, кнопки .btn и .btn.primary, палитра
// --rose / --line / --panel. Никаких новых цветов.
//
// Почему свой, а не window.confirm: нативный рисуется шрифтом системы,
// прилипает к верху окна и в китайской сборке Windows подписывает кнопки
// иероглифами. На демо банку это показывать нельзя.

export interface ConfirmRequest {
  title: string;
  text: string;
  // подпись кнопки подтверждения; действие по умолчанию опасное
  okLabel?: string;
  onOk: () => void;
}

export default function Confirm({
  request,
  onClose,
}: {
  request: ConfirmRequest | null;
  onClose: () => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);

  // Фокус на «Отмена», а не на подтверждении: диалог открывается по
  // ошибочному клику чаще, чем по осознанному, и Enter не должен
  // доделывать то, чего человек не хотел.
  useEffect(() => {
    if (request) cancel.current?.focus();
  }, [request]);

  useEffect(() => {
    if (!request) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, onClose]);

  if (!request) return null;

  return (
    <div
      className="backdrop"
      // клик мимо карточки — отмена; на самой карточке всплытие гасим
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
    >
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title">{request.title}</h2>
        <p>{request.text}</p>
        <div className="modal-actions">
          <button className="btn" ref={cancel} onClick={onClose}>
            Отмена
          </button>
          <button
            className="btn primary"
            onClick={() => {
              onClose();
              request.onOk();
            }}
          >
            {request.okLabel ?? "Удалить"}
          </button>
        </div>
      </div>
    </div>
  );
}
