# Ultra Review: как поставить и как пользоваться

Инструкция для команды. Всё делается в терминале и внутри Codex, ничего собирать не надо.

## Что это

`$ultrareview:ultrareview` в Codex запускает глубокое ревью твоих изменений: десять
независимых агентов ищут дефекты под разными углами, каждый кандидат перепроверяет
отдельный агент, подтверждённые баги воспроизводятся запуском реального кода в
одноразовой копии, потом отдельный агент ищет пропуски, ещё один судит итог, и
программа сверяет каждую цитату и каждую команду с фактами. На выходе отчёт с
находками по важности, доказательствами, предложенным исправлением и регрессионным
тестом. Код проекта ревью не трогает.

## Что нужно заранее

- Codex CLI установлен и залогинен: `codex --version` показывает 0.154 или новее.
- `git` и `python3` (на macOS есть из коробки).
- План Codex с доступными лимитами: один прогон стоит от 2 до 15 миллионов входных
  токенов, большая часть из кэша.

## Установка (один раз, две команды)

```bash
codex plugin marketplace add alexb-ww/codex-ultrareview
codex plugin add ultrareview@codex-ultrareview
```

Проверка: `codex plugin list | grep ultrareview` показывает `installed, enabled`.
Если Codex был открыт, открой новую сессию: скиллы подхватываются при старте.

## Первый запуск

1. Открой терминал в папке проекта с git-репозиторием и запусти `codex`.
2. Напиши в строке ввода:

   ```text
   $ultrareview:ultrareview
   ```

   Достаточно набрать `$ultra` и выбрать подсказку. Без параметров проверяется твоя
   ветка против основной (`origin/HEAD`, иначе `main`, иначе `master`) вместе с
   незакоммиченными правками.
3. Codex напечатает план (область, число файлов и строк, углы, модель) и начнёт
   запускать агентов. Это занимает от 10 до 30 минут, в зависимости от размера
   изменений и effort. Можно заниматься своими делами, просто не закрывай сессию.
4. В конце Codex покажет отчёт. Он же лежит в файле `report.md` в каталоге прогона
   (путь печатается в конце, обычно `$TMPDIR/ultrareview/<дата>/`).

## Типовые сценарии

```text
$ultrareview:ultrareview                                      # перед PR: ветка против основной + uncommitted
$ultrareview:ultrareview scope=changes profile=fast           # перед коммитом: только незакоммиченное, быстро
$ultrareview:ultrareview scope=branch base=develop            # ветка против другой базы
$ultrareview:ultrareview commit=abc123 repro=all              # один коммит, воспроизвести всё подтверждённое
$ultrareview:ultrareview scope=repo paths=src/auth/**         # аудит части репозитория без diff
$ultrareview:ultrareview effort=high lang=ru проверь особенно платежи   # effort агентов, язык отчёта, заметка
```

Параметры:

| Параметр | Значения | По умолчанию |
|---|---|---|
| `scope=` | `branch`, `changes`, `commit`, `repo` | `branch` |
| `base=` | ветка или коммит для `branch` | `origin/HEAD`, `main`, `master` |
| `commit=` | sha для `scope=commit` | |
| `paths=` | glob-маски через запятую для `scope=repo` | весь репозиторий |
| `profile=` | `fast` (5 углов), `standard` (9), `deep` (10) | `deep` |
| `repro=` | `auto`, `off`, `all` | `auto` |
| `votes=` | число verifier-ов на находку | `1` |
| `lang=` | `en`, `ru` | `en` |
| `model=`, `effort=` | модель и effort для агентов | из `~/.codex/config.toml` |

Любой другой текст после параметров агенты получают как приоритет («смотри особенно
auth»), но область проверки он не сужает.

## Как читать отчёт

Сначала находки по важности P0–P3. У каждой бейдж:

- **REPRODUCED**: реальный код запущен в копии и показал дефект; команда есть в журнале.
- **SOURCE-VERIFIED**: verifier назвал вход и неверный результат и процитировал строки.
- **PLAUSIBLE**: механизм реален, триггер не доказан; написано, что его подтвердит.

У каждой находки: сценарий отказа, первопричина, что пытались опровергнуть, цитаты,
воспроизведение, предложенное исправление (не применяется) и регрессионный тест.
Дальше: unresolved (проверка не завершилась или доказательства не сошлись), rejected с
причинами, таблица покрытия по углам, ограничения и паспорт прогона с моделью, числом
агентов, токенами и временем. Статус `partial` означает честное «не всё проверено»,
причины перечислены в ограничениях.

## Модель и цена

Агенты берут модель и effort из твоего `~/.codex/config.toml`; план и паспорт отчёта
показывают, что именно применилось. Замеры на gpt-6-astra:

| Профиль и effort | Агентов | Время | Входных токенов |
|---|---|---|---|
| standard, effort high, diff на 3 файла | 28 | 16 мин | 2,6 млн, из них 1,9 млн из кэша |
| deep, effort max, diff на 11 файлов Go | 20 | 27 мин | 14 млн, из них 12,8 млн из кэша |

Рекомендация на каждый день: `profile=standard effort=high`. `deep` с `max` оставь для
важных изменений перед релизом.

## Обновление и удаление

```bash
codex plugin marketplace upgrade && codex plugin add ultrareview@codex-ultrareview   # обновить
codex plugin remove ultrareview@codex-ultrareview                                     # удалить
```

## Если что-то не так

- **Codex не знает `$ultrareview:ultrareview`.** Открой новую сессию Codex; проверь
  `codex plugin list`.
- **В отчёте `MULTI_AGENT_UNAVAILABLE`.** В этой версии Codex нет sub-agents; обнови Codex.
- **Статус `partial`, много «failed: codex exited with status 1».** Кончились лимиты
  или пошли 429. Отчёт всё равно честный: непроверенное лежит в unresolved. Повтори
  позже или с `profile=fast`.
- **Воспроизведение `blocked`.** В копии нет инструментов проекта (например Go-кэш вне
  песочницы). Вердикт verifier сохраняется; в CLI-режиме можно дать
  `--repro-sandbox danger-full-access`.
- **Хочется без плагинов.** `git clone https://github.com/alexb-ww/codex-ultrareview.git`,
  затем `python3 install.py` из клона; тогда вызов просто `$ultrareview`.

## Режим из терминала (по желанию)

Тот же протокол можно запустить без интерактивного Codex, тогда каждая роль идёт
отдельным `codex exec` и все команды агентов аутентифицируются по журналу событий:

```bash
ln -sf ~/.codex/plugins/cache/codex-ultrareview/ultrareview/0.2.0/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview
ultrareview run                                   # то же, что $ultrareview:ultrareview
ultrareview run --scope changes --profile fast
ultrareview plan --scope branch --base develop    # только план, без агентов
```

Подробности флагов и протокола: `README.md` и `skills/ultrareview/references/protocol.md`.
