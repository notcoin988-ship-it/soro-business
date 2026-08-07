"""Контур безопасности воркспейса — четыре переключателя экрана 01.

ОТВЕТСТВЕННОСТЬ: одно место, где известно, что означает каждый флаг и
каково его значение по умолчанию. Читают отсюда все — ядро, площадка,
аудит; иначе «выключено» в одном модуле разойдётся с «включено» в другом.

ХРАНИЛИЩЕ: `workspaces.settings['security']` (JSONB). Отдельной таблицы
не заводим: полей четыре, они про один воркспейс, а колонка уже есть и
задумана ровно под это («тон, приветствие, языки» в комментарии DDL).

ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ — ВКЛЮЧЕНО. Ключа в JSON нет → защита работает.
Так у воркспейсов, заведённых до этой правки, ничего не отключается
задним числом, а новый флаг, добавленный в будущем, не окажется выключен
у всех разом.

`kb_only` живёт здесь ради полноты картины на экране, но выключить его
нельзя: «отвечать только по базе знаний» — это и есть продукт, а не
настройка. API запись этого флага игнорирует.
"""

from __future__ import annotations

from app.models import Workspace

# Ключ внутри `workspaces.settings`
SECURITY_KEY = "security"

# Порядок тот же, что на экране 01, — чтобы глазами сверять было легко.
KB_ONLY = "kb_only"           # отвечать только по базе знаний
CITE_SOURCES = "cite_sources"  # ссылка на источник в каждом ответе
AUDIT_LOG = "audit_log"       # аудит-лог всех обращений
MASK_PII = "mask_pii"         # маскирование персональных данных

FLAGS = (KB_ONLY, CITE_SOURCES, AUDIT_LOG, MASK_PII)
# Выключать можно всё, кроме первого — см. шапку модуля.
EDITABLE = (CITE_SOURCES, AUDIT_LOG, MASK_PII)


def security(workspace: Workspace) -> dict[str, bool]:
    """Все четыре флага с подставленными умолчаниями."""
    stored = (workspace.settings or {}).get(SECURITY_KEY) or {}
    flags = {name: bool(stored.get(name, True)) for name in FLAGS}
    flags[KB_ONLY] = True
    return flags


def enabled(workspace: Workspace, flag: str) -> bool:
    """Включён ли конкретный флаг. Читать только через эту функцию."""
    return security(workspace)[flag]


def apply(workspace: Workspace, changes: dict[str, bool]) -> dict[str, bool]:
    """Записать изменения в `workspace.settings` и вернуть новое состояние.

    Пересобираем весь словарь `settings`, а не правим на месте: SQLAlchemy
    не замечает мутацию вложенного JSONB и молча не сохраняет изменение.
    Ловушка известная и стоит дорого — настройка «сохранилась» на экране,
    а после перезагрузки вернулась.
    """
    flags = security(workspace)
    for name, value in changes.items():
        if name in EDITABLE:
            flags[name] = bool(value)

    settings = dict(workspace.settings or {})
    settings[SECURITY_KEY] = flags
    workspace.settings = settings
    return flags
