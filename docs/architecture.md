# Архитектура snapreel

snapreel — локальная настольная утилита без сервера и без сети. Пользователь
жмёт горячую клавишу, выделяет мышью прямоугольник, snapreel пишет эту область
в MP4 (или GIF) и кладёт **файл** в буфер обмена. Всё исполнение — один
короткоживущий процесс Python, который дирижирует внешним `ffmpeg` и
системными утилитами буфера обмена.

## System context (C4 L1)

```mermaid
graph LR
    user([Пользователь])
    subgraph host[Машина пользователя]
        snapreel[snapreel<br/>Python CLI]
        ffmpeg[ffmpeg / ffprobe]
        clip[Буфер обмена ОС<br/>CF_HDROP · pasteboard · X11/Wayland]
        wm[Оконная система<br/>хоткеи и оверлеи]
        fs[(~/Videos/Snapreel)]
    end
    target([Telegram · Slack · браузер · чат агента])

    user -->|хоткей, выделение мышью| snapreel
    snapreel -->|запуск и останов| ffmpeg
    ffmpeg -->|захват пикселей| wm
    ffmpeg -->|MP4 / GIF| fs
    snapreel -->|кладёт файл| clip
    user -->|Ctrl+V| target
    clip --> target
```

## Containers (C4 L2)

Контейнер здесь ровно один — CLI-процесс. Показаны его модули и внешние
исполняемые файлы, потому что именно на их границе живут все сложности.

```mermaid
graph TD
    cli[cli.py<br/>аргументы, вывод, диалоги]
    rec[recorder.py<br/>сценарий записи]
    sel[selector.py<br/>оверлей выделения · Tk]
    ind[indicator.py<br/>рамка и таймер · Tk]
    back[backends/<br/>gdigrab · x11grab · wf-recorder · avfoundation]
    clip[clipboard/<br/>CF_HDROP · osascript · xclip · wl-copy]
    enc[encode.py<br/>GIF и probe]
    store[storage.py<br/>имена файлов, retention]
    cfg[config.py<br/>TOML + env]
    plat[platform_info.py<br/>детект платформы, DPI, границы экрана]
    deps[deps.py<br/>системные зависимости]
    auto[autostart.py<br/>системный хоткей]
    daemon[hotkeys.py<br/>демон pynput]

    cli --> rec
    cli --> deps
    cli --> auto
    cli --> daemon
    daemon --> rec
    rec --> sel
    rec --> ind
    rec --> back
    rec --> clip
    rec --> enc
    rec --> store
    rec --> plat
    back --> plat
    clip --> plat
    cli --> cfg
    rec --> cfg
```

## Поток записи

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant C as cli
    participant R as recorder
    participant S as selector (Tk)
    participant B as backend
    participant F as ffmpeg
    participant I as indicator (Tk)
    participant K as clipboard

    U->>C: snapreel record
    C->>R: record(config)
    R->>S: select_region()
    S-->>R: Region (физические пиксели)
    R->>R: clamp + normalize (чётные стороны)
    R->>B: start(region, path, max_seconds)
    B->>F: запуск процесса
    R->>I: показать рамку и таймер
    U->>I: Esc или «Стоп»
    I->>B: request_stop()
    B->>F: 'q' в stdin (или SIGINT для wf-recorder)
    F-->>R: файл дописан
    R->>K: положить файл в буфер
    R-->>U: путь, размер, длительность
```

## Границы и правила

- **Платформенная развилка — только в двух местах**: выбор бэкенда захвата
  (`backends/__init__.py`) и выбор реализации буфера (`clipboard/__init__.py`).
  Оба спрашивают `platform_info.detect()`. Всё остальное платформо-независимо.
- **Ядро без побочных эффектов**: `region`, `config`, `storage`, `deps`,
  `autostart` — чистые функции и данные. Они и покрыты тестами плотнее всего.
- **Внешние процессы всегда за фасадом.** ffmpeg вызывается только из
  `backends/*` и `encode.py`; системные утилиты — только из `clipboard/*`,
  `autostart.py`, `deps.py`, `notify.py`.
- **GUI необязателен.** `selector` и `indicator` подтягиваются лениво: без
  tkinter работают `doctor`, `config`, `prune`, а запись — через `--region`.

## Что где лежит

| Путь | Содержимое |
|---|---|
| `src/snapreel/` | пакет |
| `src/snapreel/backends/` | захват экрана, по файлу на платформу |
| `src/snapreel/clipboard/` | буфер обмена: `windows.py` (ctypes), `posix.py` (macOS + Linux) |
| `tests/` | pytest, без экрана и без ffmpeg |
| `scripts/` | установщики для чистой машины, sync-скрипт правил агентов |
| `docs/adr/` | принятые решения и их причины |

## Где будет больно

- **Wayland.** Поддержан только wlroots (`wf-recorder`). GNOME и KDE со своими
  протоколами записи потребуют отдельного бэкенда через xdg-desktop-portal
  и PipeWire — это самая крупная известная дыра.
- **Мультимонитор на macOS.** avfoundation снимает один экран; область,
  пересекающая границу дисплеев, обрежется.
- **Буфер обмена в X11** держится процессом `xclip`; если пользователь убьёт
  его, вставка перестанет работать до следующей записи.
