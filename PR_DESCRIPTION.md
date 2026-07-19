# PR: fix(kanban): 4 lifecycle bugs — promote with `--force`, `claim+assign`, stale cleanup

**Ветка:** `fix/kanban-promote-and-lifecycle`
**Коммит:** `0c25c06`

Автор плагина, привет. Я протестировал твой pipeline plugin в реальных условиях — запускал пайплайны, перезагружал сессии, смотрел на дашборд. Нашёл 4 проблемы с жизненным циклом канбан-задач. Вот детали.

---

## Bug #1: `promote()` падает с `unsatisfied parent dependencies`

**Где:** `kanban.py:promote()`

**Симптом:** При создании пайплайна первый агент (finder) промоутится в `ready`, но `hermes kanban promote` возвращает ошибку:

```
error: unsatisfied parent dependencies: t_XXXX (use --force to override)
```

**Корень:** Родительская задача (пайплайн) живёт в статусе `ready`, а не `running`. Когда `promote` проверяет parent dependencies — видит, что родитель не `running`, и отказывает. После этого finder остаётся `todo`, никто не запускается.

**Фикс:** Добавлен параметр `force: bool = False` в `promote()`. Все вызовы из `create_task_tree()` и `advance()` идут с `force=True`.

**Урок:** `hermes kanban promote --force` — единственный способ сделать promote, когда родительская задача сама находится в `ready`, а это нормальное состояние для pipeline parent (они никогда не claim-ятся воркером).

---

## Bug #2: `started_at` и `assignee` никогда не заполняются

**Где:** `kanban.py:advance()`, `kanban.py:create_task_tree()`

**Симптом:** У всех 62 дочерних задач (100%) поля пустые:

```
62 child tasks with empty started_at
61 child tasks with empty assignee
```

**Корень:** Исходный код вызывал только `promote(task_id)`, который переводит `todo → ready`. Он НЕ проставляет `started_at` и НЕ ставит статус `running`. Никакого claim-а не происходило.

**Фикс:** После `promote()` добавлен вызов `_claim_and_assign()`, который делает:
1. `hermes kanban claim --json <task>` — переводит `ready → running`, проставляет `started_at` и `claim_id`
2. `hermes kanban assign <task> @agent` — заполняет `assignee`

Теперь каждый запущенный агент имеет `running` статус, `started_at` и `assignee`.

---

## Bug #3: Пайплайны не чистятся при смене сессии (`/new`)

**Где:** `kanban.py:scan_board()`, `kanban.py:_cleanup_stale_pipelines()` (новое)

**Симптом:** После `/new` (сброса сессии) или просто падения соединения — `pipeline_resume()` не находит активный пайплайн. Почему? Потому что родитель в `ready`, а `scan_board()` ищет сначала `running`, потом `ready`, потом детей... но находит старые, накапливает мусор.

На момент проверки на доске висело **3 мёртвых пайплайна** в `ready`, каждый с 4-9 детьми в `todo`. Никто их не чистит.

**Фикс:** Добавлена функция `_cleanup_stale_pipelines(max_age_hours=24)`, которая:
1. Находит pipeline parents в статусе `ready`, старше 24 часов
2. Архивирует всех детей
3. Архивирует родителя
4. Вызывается в начале `scan_board()` перед поиском активного пайплайна

---

## Bug #4: `pipeline_save` первый promote не сопровождается claim-ом

**Где:** `kanban.py:create_task_tree()`

**Симптом:** Даже если `--force` победил parent dependency — finder оставался `ready`, а не `running`. В `started_at` — пусто. В assignee — пусто.

**Фикс:** Теперь `create_task_tree()` сразу после `promote(first_id, force=True)` вызывает `_claim_and_assign(first_id, @{first_agent})`.

---

## Как тестировать

```bash
# Симлинк на новую версию плагина
ln -sf ~/git/hermes-pipeline-plugin ~/.hermes/plugins/pipeline

# Запустить пайплайн
# После pipeline_save проверить что finder имеет running + started_at + assignee
hermes kanban --board pipeline show --json <task_id> | python3 -m json.tool

# Сбросить сессию (/new) и запустить pipeline_resume
# scan_board должен подхватить активный пайплайн
```
