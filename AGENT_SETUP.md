# Ultra Review для Codex: установка и использование

Этот файл можно отдать агенту (Codex, Claude Code и т.п.) целиком: «прочитай и сделай».
Цель: установить плагин Ultra Review в Codex CLI и провести первое ревью.

## Шаг 1. Проверить окружение

Выполни и убедись, что версия 0.154 или новее:

    codex --version

Нужны также `git` и `python3` (проверь `git --version`, `python3 --version`).
Если `codex` не найден или не залогинен, остановись и сообщи пользователю: нужен
установленный и авторизованный Codex CLI.

## Шаг 2. Установить плагин

    codex plugin marketplace add alexb-ww/codex-ultrareview
    codex plugin add ultrareview@codex-ultrareview

Проверка успеха:

    codex plugin list | grep ultrareview

Ожидаемый вывод содержит `ultrareview@codex-ultrareview  installed, enabled`.
Если Codex сейчас открыт, пользователю нужно открыть новую сессию: скиллы
подхватываются при старте.

## Шаг 3. Провести ревью

В интерактивной сессии Codex, открытой в корне git-репозитория проекта, пользователь
(или агент внутри Codex) пишет:

    $ultrareview:ultrareview

Без параметров проверяется текущая ветка против основной (origin/HEAD, иначе main,
иначе master) вместе с незакоммиченными правками. Прогон занимает 10–30 минут,
сессию закрывать нельзя. В конце Codex показывает отчёт; он же сохраняется в файл
`report.md` в каталоге прогона (путь печатается в конце, обычно
`$TMPDIR/ultrareview/<дата>/`).

Варианты:

    $ultrareview:ultrareview scope=changes profile=fast      только незакоммиченное, быстро
    $ultrareview:ultrareview scope=branch base=develop       ветка против другой базы
    $ultrareview:ultrareview commit=<sha> repro=all          один коммит, воспроизвести всё
    $ultrareview:ultrareview scope=repo paths=src/auth/**    аудит части репозитория
    $ultrareview:ultrareview effort=high lang=ru <заметка>   effort агентов, язык отчёта, приоритет

Параметры: `scope=branch|changes|commit|repo`, `base=`, `commit=`, `paths=`,
`profile=fast|standard|deep` (по умолчанию deep), `repro=auto|off|all`, `votes=`,
`lang=en|ru`, `model=`, `effort=`. Любой другой текст становится приоритетом для
агентов, область он не сужает. Рекомендация на каждый день: `profile=standard effort=high`.

## Шаг 4. Как читать отчёт

Находки идут по важности P0–P3 с бейджами: REPRODUCED (код запущен и показал дефект),
SOURCE-VERIFIED (вход и неверный результат названы, строки процитированы), PLAUSIBLE
(механизм реален, триггер не доказан). Дальше unresolved, rejected с причинами,
покрытие, ограничения и паспорт прогона. Статус `partial` означает «не всё проверено»,
причины перечислены в ограничениях. Ревью ничего не исправляет и не коммитит.

## Обновление и удаление

    codex plugin marketplace upgrade && codex plugin add ultrareview@codex-ultrareview
    codex plugin remove ultrareview@codex-ultrareview

## Если что-то не так

- Codex не знает `$ultrareview:ultrareview`: новая сессия Codex, затем `codex plugin list`.
- `MULTI_AGENT_UNAVAILABLE`: в этой версии Codex нет sub-agents, обновить Codex.
- `partial` и много `failed: codex exited with status 1`: кончились лимиты или ответы 429;
  отчёт честный, непроверенное в unresolved; повторить позже или с `profile=fast`.
- Воспроизведение `blocked`: в копии нет инструментов проекта; вердикт verifier сохраняется.
- Без плагинов: `git clone https://github.com/alexb-ww/codex-ultrareview.git` и
  `python3 install.py` из клона; тогда вызов просто `$ultrareview`.

Репозиторий: https://github.com/alexb-ww/codex-ultrareview
Этот файл: https://raw.githubusercontent.com/alexb-ww/codex-ultrareview/main/AGENT_SETUP.md
