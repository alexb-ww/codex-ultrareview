# Ultra Review для Codex CLI (v0.2.0)

Глубокое многоагентное ревью кода для Codex CLI: независимые finder-агенты, отдельный
verifier на каждую находку, воспроизведение в одноразовой копии репозитория, sweep по
пропускам, независимый adjudicator и детерминированный gate, который сверяет каждую
цитату со снимком файлов и каждую заявленную команду с реальным журналом выполнения.

Два входа, один протокол:

| Вход | Где работает | Изоляция агентов | Аутентификация команд |
|---|---|---|---|
| `ultrareview run` (драйвер) | терминал, `codex exec` на каждую роль | гарантирована процессом | да, по событиям `--json` |
| `$ultrareview` (skill) | интерактивный Codex, `spawn_agent` с `fork_turns: "none"` | по инструкции координатору | нет (в отчёте сказано явно) |

Python 3.9+, только стандартная библиотека. Ничего не скачивается, `config.toml` не
меняется, исходники проекта никогда не редактируются.

## Установка

```bash
cd codex-ultrareview-kit-v2
python3 -m unittest discover -s tests -t .      # 130+ тестов, все через fake codex
python3 install.py --dry-run
python3 install.py                              # ~/.agents/skills/ultrareview + kit/
ln -s ~/.agents/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview
```

`python3 install.py --with-hooks` дополнительно кладёт PreToolUse-guard в
`~/.codex/hooks.json` (если файла ещё нет; иначе печатает сниппет для ручного слияния).
Guard запрещает правки файлов, пока существует маркер `$TMPDIR/ultrareview/REVIEW_ACTIVE`.

## Драйвер

```bash
ultrareview run                                  # ветка против origin/HEAD|main|master + uncommitted
ultrareview run --scope changes --profile fast   # только незакоммиченное, 5 углов
ultrareview run --scope commit --commit HEAD~1   # один коммит
ultrareview run --scope repo --paths 'src/auth/**' --jobs 6
ultrareview plan --scope branch --base develop   # только план, без агентов
```

Ключевые флаги: `--profile fast|standard|deep` (5/9/10 углов, по умолчанию deep),
`--votes N`, `--repro auto|off|all` и `--max-repro`, `--max-findings`, `--jobs`,
`--agent-timeout`, `--max-files/--max-lines` (по умолчанию 500/8000, как у облачного
ultrareview), `--model`, `--effort` и `--effort-<роль>` (`finder`, `verifier`,
`reproducer`, `adjudicator`, `mapper`, `triage`, `sweep`), `--lang en|ru` (язык текстов
агентов), `--keep-sessions`, `--keep-worktree`, `--no-preamble`, `--run-dir`.

Модель и effort по умолчанию наследуются из `~/.codex/config.toml`. Каждый агент — свой
`codex exec --ephemeral --json --output-schema -o`, stdin закрыт, брифы и события лежат
в каталоге прогона (`~/.cache/ultrareview/runs/<stamp>-<repo>/`): `agents/*.brief.md`,
`agents/*.events.jsonl`, `agents/*.output.json`, `ledger.jsonl`, `diff.patch`,
`snapshot.json`, `state.json`, `report.md`, `report.json`, `run.json`.

Коды выхода: 0 — завершено; 1 — ошибка области или запуска (ничего не потрачено);
3 — завершено, но partial или есть ошибки gate.

## Skill

Внутри Codex: `$ultrareview scope=branch base=main profile=deep`. Координатор вызывает
`scripts/ultrareview.py step`, получает манифест агентов, запускает каждого через
`spawn_agent` с `fork_turns: "none"`, записывает их JSON в указанные файлы и повторяет
`step`, пока не появится отчёт. Подробности в `skill/ultrareview/SKILL.md` и
`references/protocol.md`.

## Что делает результат сильнее обычного ревью

- Каждая находка проходит НОВОГО verifier с трёхзначным вердиктом CONFIRMED / PLAUSIBLE /
  REFUTED; REFUTED допустим только с конструктивным опровержением из кода. Гонки и редкие
  ветки не отбрасываются как «спекуляция».
- Finder-ы работают под углами, заточенными под diff (построчный скан, аудит удалённого
  поведения, трассировка вызовов, ловушки языка, обёртки/прокси), плюс доменные углы.
- Воспроизведение идёт против реального кода в одноразовом worktree, а gate требует,
  чтобы команда воспроизведения была в журнале выполнения именно этого агента.
- Sweep ищет только то, чего нет в подтверждённом списке; adjudicator не может повысить
  вердикт и не может добавить находку без нового verifier.
- Отчёт всегда содержит покрытие, ограничения и паспорт прогона; «ошибок нет» не
  утверждается, только «в проверенной области не найдено».

## Корпус для оценки

```bash
python3 corpus/build.py python-svc /tmp/ur-corpus            # main = корректный код, feature = 5 дефектов
ultrareview run --repo /tmp/ur-corpus --scope branch --base main --run-dir /tmp/ur-corpus-run
python3 scripts/eval_corpus.py /tmp/ur-corpus-run/report.json /tmp/ur-corpus.expected.json
```

Скрипт печатает recall по заложенным дефектам, попадания в decoy и precision.

## Ограничения

- Skill-режим не аутентифицирует команды агентов (у sub-agents нет отдельного журнала).
- Воспроизведение требует установленных инструментов проекта; иначе `blocked` с
  причиной, вердикт verifier сохраняется.
- Gate доказывает структуру и подлинность цитат/команд, но не истинность рассуждений;
  для этого есть независимый adjudicator и, в конечном счёте, человек.
