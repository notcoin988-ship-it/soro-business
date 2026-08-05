import { useState } from "react";
import { ApiError, login } from "../lib/api";

// Вход в консоль (раздел 9). Пока бэкенда нет — форма уже настоящая,
// но при недоступном /api/auth/login пускает внутрь и говорит об этом.
// Так каркас можно смотреть до того, как появится auth.py.
export default function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(password);
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Неверный пароль");
      } else {
        setError("Бэкенд недоступен — открываю без проверки");
        setTimeout(onDone, 900);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form onSubmit={submit}>
        <h1>Soro Business</h1>
        <p>Банк Эсхата — демо-воркспейс</p>
        <input
          type="password"
          placeholder="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        <div className="err">{error}</div>
        <button type="submit" disabled={busy}>
          {busy ? "Проверяю…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
