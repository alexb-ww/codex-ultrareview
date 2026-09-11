# Ultra Review для Codex CLI

Глубокое многоагентное ревью кода для Codex CLI: независимые finder-агенты, отдельный
verifier на каждую находку, воспроизведение в одноразовой копии репозитория, sweep по
пропускам, независимый adjudicator и детерминированный gate, который сверяет каждую
цитату со снимком файлов и каждую заявленную команду с журналом выполнения агента.

Python 3.9+, только стандартная библиотека. Ничего не скачивается, `config.toml` не
меняется, исходники проекта не редактируются.

Пошаговая инструкция для команды: [INSTALL.md](INSTALL.md).

## Установка (две команды, без клонов и симлинков)

```bash
codex plugin marketplace add alexb-ww/codex-ultrareview
codex plugin add ultrareview@codex-ultrareview
```

Открыть новую сессию Codex и в каталоге проекта написать:

```text
$ultrareview:ultrareview
```

Автодополнение подставит имя после `$ultra`. Обновление до свежей версии:

```bash
codex plugin marketplace upgrade && codex plugin add ultrareview@codex-ultrareview
```

Требования: авторизованный Codex CLI с поддержкой plugins и sub-agents (проверено на
0.154.0), git, python3. Слэш-команд вида `/ultrareview` в Codex для своих скиллов нет:
пользовательские скиллы вызываются через `$имя`.

Альтернатива без плагинов: `git clone https://github.com/alexb-ww/codex-ultrareview.git`
и `python3 install.py` из клона кладут скилл в `~/.agents/skills/ultrareview`, тогда
вызов короче, просто `$ultrareview`; обновление через повторный `git pull` и
`python3 install.py --force`.

## Как пользоваться

```text
$ultrareview:ultrareview                                   # ветка против origin/HEAD|main|master + uncommitted
$ultrareview:ultrareview scope=changes profile=fast        # только незакоммиченное, 5 углов
$ultrareview:ultrareview scope=branch base=develop lang=ru # своя база, тексты агентов по-русски
$ultrareview:ultrareview commit=abc123 repro=all           # один коммит, воспроизводить всё
$ultrareview:ultrareview effort=high проверь особенно auth # effort для агентов + заметка-приоритет
```

Аргументы: `scope=branch|changes|commit|repo`, `base=`, `commit=`, `paths=`,
`profile=fast|standard|deep`, `repro=auto|off|all`, `votes=`, `lang=en|ru`, `model=`,
`effort=`. Остальной текст становится приоритетом для агентов, но не сужает область.
Скилл вызывается только явно; сам Codex его не запускает.

## На какой модели идёт ревью

Скилл и драйвер модель не выбирают: каждый агент наследует `model` и
`model_reasoning_effort` из `~/.codex/config.toml`. План и паспорт отчёта показывают, что
именно применилось и откуда (флаг, config.toml или умолчание Codex). Переопределить:

- в скилле `model=<имя>` и `effort=<уровень>` для всех агентов;
- в CLI `--model`, `--effort` для всех ролей и `--effort-finder`, `--effort-verifier`,
  `--effort-reproducer`, `--effort-adjudicator`, `--effort-mapper`, `--effort-triage`,
  `--effort-sweep` по ролям. Рабочий рецепт: `--effort high --effort-verifier xhigh
  --effort-adjudicator xhigh`, finder-ам max не нужен, verifier-ам нужен.

## CLI из терминала

Для полной аутентификации команд (каждая роль отдельным `codex exec` с журналом событий)
есть драйвер. Launcher лежит внутри установленного скилла; удобнее сделать симлинк:

```bash
# plugin-установка (путь содержит версию плагина)
ln -sf ~/.codex/plugins/cache/codex-ultrareview/ultrareview/0.2.0/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview
# установка через install.py
ln -sf ~/.agents/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview

ultrareview run                                  # то же, что $ultrareview
ultrareview run --scope changes --profile fast
ultrareview run --scope repo --paths 'src/auth/**' --jobs 6
ultrareview plan --scope branch --base develop   # только план, без агентов
```

Ключевые флаги: `--profile`, `--votes`, `--repro` и `--max-repro`, `--max-findings`,
`--jobs`, `--agent-timeout`, `--max-files/--max-lines` (500/8000 как у облачного
ultrareview), `--lang`, `--note`, `--repro-sandbox workspace-write|danger-full-access`
(второе нужно Go-проектам, чтобы `go test` видел системный GOCACHE), `--keep-sessions`,
`--keep-worktree`, `--no-preamble`, `--run-dir`.

Артефакты прогона: `~/.cache/ultrareview/runs/<stamp>-<repo>/` (в режиме скилла
`$TMPDIR/ultrareview/`): `report.md`, `report.json`, `agents/*.brief.md`,
`agents/*.events.jsonl`, `agents/*.output.json`, `ledger.jsonl`, `diff.patch`,
`snapshot.json`, `state.json`. Коды выхода: 0 завершено; 1 ошибка области или запуска
(токены не потрачены); 3 завершено, но partial или есть проблемы с доказательствами.

## Как читать отчёт

Сначала находки по убыванию severity P0–P3, у каждой бейдж: REPRODUCED (реальный код
запущен и показал дефект, команда есть в журнале агента), SOURCE-VERIFIED (verifier
назвал вход и неверный результат и процитировал строки), PLAUSIBLE (механизм реален,
триггер не доказан, сказано, что его подтвердит). Затем unresolved (verifier не
завершился или доказательства не прошли проверку), rejected с причинами, таблица
покрытия по углам, ограничения и паспорт прогона. «Ошибок нет» отчёт не утверждает,
только «в проверенной области не найдено».

Режим скилла не аутентифицирует команды sub-agents (у них нет отдельного журнала);
отчёт пишет об этом. Для полной аутентификации есть CLI.

## Стоимость и время

Замеры на Codex 0.154.0, gpt-6-astra:

| Профиль и effort | Агентов | Время | Входных токенов |
|---|---|---|---|
| standard, finder high / verifier xhigh, diff 3 файла | 28 | 16 мин | 2,6 млн (1,9 млн из кэша) |
| deep, effort max, diff 11 файлов Go | 20 до лимита | 27 мин | 14 млн (12,8 млн из кэша) |

Для повседневной работы разумен `profile=standard effort=high`.

## Проверка качества

```bash
python3 -m unittest discover -s tests -t .                      # 155 тестов через fake codex
python3 corpus/build.py python-svc /tmp/ur-corpus                # main корректен, feature с 5 дефектами
ultrareview run --repo /tmp/ur-corpus --base main --run-dir /tmp/ur-run
python3 scripts/eval_corpus.py /tmp/ur-run/report.json /tmp/ur-corpus.expected.json
```

На этом корпусе реальный прогон нашёл и воспроизвёл все пять дефектов, не тронул
ловушку и отклонил два pre-existing бага с цитатами.

## Ограничения

- Воспроизведение требует установленных инструментов проекта; иначе `blocked` с
  причиной, вердикт verifier сохраняется. В песочнице Codex `git worktree add`
  запрещён, поэтому копия делается через `git archive`.
- Gate доказывает структуру и подлинность цитат и команд, но не истинность рассуждений;
  для этого есть независимый adjudicator и, в конечном счёте, человек.
- Один прогон deep на max effort стоит десятки миллионов токенов; лимиты аккаунта
  превращают прогон в честный `partial`, а не в тихий пропуск.

Раскладка репозитория: `skills/ultrareview/` (SKILL.md, agents, scripts, references и
`kit/` с драйвером, промптами, хуками и launcher-ом), `.codex-plugin/plugin.json`,
`.agents/plugins/marketplace.json`, `tests/`, `corpus/`, `SPEC.md`. Протокол:
`skills/ultrareview/references/protocol.md`. История: `CHANGELOG.md`.
