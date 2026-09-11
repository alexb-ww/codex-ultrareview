# Ultra Review для Codex CLI

Глубокое многоагентное ревью кода для Codex CLI: независимые finder-агенты, отдельный
verifier на каждую находку, воспроизведение в одноразовой копии репозитория, sweep по
пропускам, независимый adjudicator и детерминированный gate, который сверяет каждую
цитату со снимком файлов и каждую заявленную команду с журналом выполнения агента.

Корень этого репозитория и есть скилл Codex: `SKILL.md`, `agents/`, `scripts/`,
`references/` и `kit/` с драйвером. Python 3.9+, только стандартная библиотека.
Ничего не скачивается, `config.toml` не меняется, исходники проекта не редактируются.

## Установка для команды (одна минута)

```bash
git clone https://github.com/alexb-ww/codex-ultrareview.git ~/.agents/skills/ultrareview
ln -sf ~/.agents/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview   # CLI, по желанию
```

Обновление: `git -C ~/.agents/skills/ultrareview pull`. Перезапустить Codex один раз,
чтобы скилл появился. Требования: установленный и авторизованный Codex CLI с
поддержкой sub-agents (проверено на 0.154.0), git, python3.

Кому не хочется держать git-клон в каталоге скиллов: `python3 install.py` копирует
только нужные файлы туда же; `--with-hooks` дополнительно ставит PreToolUse-guard,
который запрещает правки файлов, пока идёт ревью.

## Как пользоваться

Внутри Codex, в каталоге проекта:

```text
$ultrareview                                   # ветка против origin/HEAD|main|master + uncommitted
$ultrareview scope=changes profile=fast         # только незакоммиченное, 5 углов
$ultrareview scope=branch base=develop lang=ru  # своя база, тексты агентов по-русски
$ultrareview commit=abc123 repro=all            # один коммит, воспроизводить всё
```

Скилл вызывается только явно; сам Codex его не запускает. Любой текст после параметров
(например «проверь особенно auth») становится приоритетом для агентов, но не сужает
область.

Из терминала, с полной аутентификацией команд (каждая роль отдельным `codex exec`):

```bash
ultrareview run                                  # то же, что $ultrareview
ultrareview run --scope changes --profile fast
ultrareview run --scope repo --paths 'src/auth/**' --jobs 6
ultrareview plan --scope branch --base develop   # только план, без агентов
```

Ключевые флаги: `--profile fast|standard|deep` (5/9/10 углов, по умолчанию deep),
`--votes N`, `--repro auto|off|all` и `--max-repro`, `--max-findings`, `--jobs`,
`--agent-timeout`, `--max-files/--max-lines` (500/8000 как у облачного ultrareview),
`--model`, `--effort` и `--effort-<роль>`, `--lang en|ru`, `--note "<текст>"`,
`--repro-sandbox workspace-write|danger-full-access` (второе нужно Go-проектам, чтобы
`go test` видел системный GOCACHE), `--keep-sessions`, `--keep-worktree`,
`--no-preamble`, `--run-dir`.

Модель и effort по умолчанию наследуются из `~/.codex/config.toml`. Артефакты прогона
лежат в `~/.cache/ultrareview/runs/<stamp>-<repo>/` (в режиме скилла в `$TMPDIR/ultrareview/`):
`report.md`, `report.json`, `agents/*.brief.md`, `agents/*.events.jsonl`,
`agents/*.output.json`, `ledger.jsonl`, `diff.patch`, `snapshot.json`, `state.json`.

Коды выхода: 0 завершено; 1 ошибка области или запуска (токены не потрачены);
3 завершено, но partial или есть проблемы с доказательствами.

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

Для повседневной работы разумен `profile=standard` с `--effort high --effort-verifier xhigh`.

## Проверка качества

```bash
python3 -m unittest discover -s tests -t .                      # 152 теста через fake codex
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

Подробный протокол: `references/protocol.md`. История изменений: `CHANGELOG.md`.
