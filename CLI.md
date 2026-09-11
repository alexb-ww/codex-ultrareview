# Ultra Review из терминала

Тот же протокол, что и `$ultrareview:ultrareview` в Codex, но каждая роль идёт отдельным
`codex exec`, поэтому все команды агентов аутентифицируются по журналу событий. Нужен
установленный и залогиненный Codex CLI 0.154+, git, python3.

## Установка (один раз)

    git clone https://github.com/alexb-ww/codex-ultrareview.git ~/.codex-ultrareview
    mkdir -p ~/.local/bin
    ln -sf ~/.codex-ultrareview/skills/ultrareview/kit/bin/ultrareview ~/.local/bin/ultrareview

Если `ultrareview --version` отвечает «command not found», добавь ~/.local/bin в PATH:

    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

Проверка: `ultrareview --version` печатает `ultrareview 0.2.0`.

Обновление: `git -C ~/.codex-ultrareview pull`.

## Запуск

В корне git-репозитория проекта:

    ultrareview run                                          # ветка против origin/HEAD|main|master + uncommitted
    ultrareview run --scope changes --profile fast           # только незакоммиченное, быстро
    ultrareview run --scope branch --base develop --lang ru  # своя база, отчёт по-русски
    ultrareview run --scope commit --commit abc123 --repro all
    ultrareview run --scope repo --paths 'src/auth/**'
    ultrareview plan --scope branch --base main              # только план, агенты не запускаются

Рекомендуемый набор на каждый день:

    ultrareview run --profile standard --effort high --effort-verifier xhigh --effort-adjudicator xhigh --jobs 3 --lang ru

`--jobs 3` держит параллельность низкой, чтобы не ловить 429 от API. Для Go-проектов
добавь `--repro-sandbox danger-full-access`, иначе `go test` в копии не увидит системный
GOCACHE и воспроизведение будет `blocked`.

Прогон идёт 10–30 минут. Прогресс печатается в stderr, итоговый отчёт в stdout и в файлы:

    ~/.cache/ultrareview/runs/<дата>-<репозиторий>/report.md
    ~/.cache/ultrareview/runs/<дата>-<репозиторий>/report.json

Там же брифы и журналы каждого агента (`agents/`), `ledger.jsonl` со всеми выполненными
командами, `diff.patch`, `snapshot.json`.

Коды выхода: 0 завершено; 1 ошибка области или запуска (токены не потрачены);
3 завершено, но partial или есть проблемы с доказательствами.

## Полезные флаги

    --profile fast|standard|deep       5 / 9 / 10 углов (по умолчанию deep)
    --effort <уровень>                 effort всех агентов; --effort-finder, --effort-verifier,
                                       --effort-reproducer, --effort-adjudicator по ролям
    --model <имя>                      модель вместо той, что в ~/.codex/config.toml
    --votes N                          verifier-ов на находку (по умолчанию 1)
    --repro auto|off|all, --max-repro  воспроизведение (по умолчанию auto, до 6)
    --max-findings N                   потолок находок в отчёте (по умолчанию 15)
    --max-files N, --max-lines N       лимиты диффа (500 / 8000), выше отказ до траты токенов
    --note "<текст>"                   приоритет для агентов, область не сужает
    --lang en|ru                       язык текстов агентов
    --jobs N, --agent-timeout сек      параллельность и таймаут одного агента
    --run-dir <путь>                   куда писать артефакты (вне репозитория)
    --keep-sessions                    не удалять сессии codex после прогона

Стоимость: standard с effort high на дифф в 3 файла около 2,6 млн входных токенов
(1,9 млн из кэша), 16 минут; deep с max на 11 файлов около 14 млн, 27 минут.

Репозиторий: https://github.com/alexb-ww/codex-ultrareview
Этот файл: https://raw.githubusercontent.com/alexb-ww/codex-ultrareview/main/CLI.md
