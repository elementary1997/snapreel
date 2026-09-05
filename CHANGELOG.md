# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

## [0.1.0] — 2026-09-05

Первая версия. Требуется Python 3.11+ (нижнюю границу задаёт `tomllib`).

### Добавлено

- Запись выделенной мышью области экрана в MP4 (H.264) длительностью 5–60 секунд.
- Готовый файл кладётся в буфер обмена: `CF_HDROP` на Windows, `POSIX file`
  на macOS, `text/uri-list` в X11 и Wayland.
- Конвертация в анимированный GIF по флагу `--gif` (палитра в два прохода).
- Четыре бэкенда захвата: gdigrab (Windows), avfoundation (macOS),
  x11grab (Linux/X11), wf-recorder (Linux/wlroots).
- Оверлей выделения области и индикатор записи с таймером и кнопкой «Стоп».
- `snapreel setup` — установка системных зависимостей через apt/dnf/pacman/
  zypper/brew/winget с подтверждением.
- Горячая клавиша задаётся пользователем в свободном формате
  (`Ctrl+Alt+5`, `Win+Shift+S`); автоматическая регистрация в GNOME и Windows,
  для остальных окружений печатается готовая команда.
- `snapreel daemon` — глобальный хоткей через pynput (Windows, X11, macOS).
- `snapreel doctor` — диагностика окружения; работает и при отсутствующих
  tkinter, ffmpeg или в WSL.
- Автосохранение клипов в `~/Videos/Snapreel` с очисткой старше `keep_days`.
- Конфигурация в TOML с перекрытием через переменные `SNAPREEL_*`.
- Установщики `scripts/install.sh` и `scripts/install.ps1` для чистой машины.
- Одиночные бинарники PyInstaller для Linux, macOS (arm64 и x86_64) и Windows;
  собираются по тегу `v*` и выкладываются в GitHub Release.

### Известные ограничения

- Wayland поддержан только на композиторах wlroots; GNOME и KDE — нет.
- На macOS запись идёт с одного экрана.
- Изнутри WSL доступен только экран WSLg, а не рабочий стол Windows.
