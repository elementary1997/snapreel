#!/usr/bin/env bash
# Сборка минимального ffmpeg для вшивания в релизный бинарник snapreel.
#
#   ./scripts/build-ffmpeg.sh                 # соберёт в build/ffmpeg-prefix
#   OUT=/tmp/bin ./scripts/build-ffmpeg.sh    # положит готовый ffmpeg в /tmp/bin
#
# Готовые статические сборки весят под 140 МБ, потому что собраны со всеми
# кодеками мира. snapreel пользуется единицами из них, поэтому здесь ffmpeg
# собирается с `--disable-everything` и списком того, что действительно нужно
# (см. ADR-0006). Всё, что не перечислено ниже, в бинарник не попадёт — и
# наоборот: забытая строка означает отказ уже у пользователя, поэтому в конце
# скрипт проверяет собранное на наличие каждой заявленной возможности.
set -euo pipefail

# 7.1.2, не ниже: в 7.1.1 сборка только с H.264 не линкуется —
# h2645_sei.c зовёт ff_aom_uninit_film_grain_params, а aom_film_grain.o
# собирался тогда лишь вместе с HEVC (libavcodec/Makefile)
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1.2}"
X264_VERSION="${X264_VERSION:-31e19f92f00c7003fa115047ce50978bc98c3a0d}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="${WORK:-$root/build/ffmpeg-src}"
prefix="${PREFIX:-$root/build/ffmpeg-prefix}"
out="${OUT:-$root/build/ffmpeg-bin}"
jobs="${JOBS:-$( (nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4) )}"

case "$(uname -s)" in
    Linux) platform=linux; exe="" ;;
    Darwin) platform=macos; exe="" ;;
    MINGW* | MSYS* | CYGWIN*) platform=windows; exe=".exe" ;;
    *)
        echo "неизвестная система: $(uname -s)" >&2
        exit 1
        ;;
esac

# --- что должно оказаться внутри -----------------------------------------
#
# Кодеки: H.264 через libx264 — им пишется клип; gif — в него конвертируется
# готовый MP4; aac — только для записи со звуком (`capture_audio`).
# Декодеры нужны обратной дорогой: чтобы собрать GIF, ffmpeg читает свой же
# MP4. rawvideo и bmp — это то, чем отдают кадры устройства захвата.
encoders="libx264,gif,aac"
decoders="h264,aac,rawvideo,bmp,pcm_s16le,pcm_f32le"
muxers="mp4,gif,rawvideo,image2,null"
demuxers="mov,gif,image2,rawvideo,aac,h264"
parsers="h264,aac"
protocols="file,pipe"

# Фильтры: fps/scale/split/palettegen/paletteuse — двухпроходная палитра GIF;
# crop — вырезание области на macOS, где avfoundation отдаёт экран целиком;
# format/aformat/aresample/anull/null/copy/setpts ffmpeg вставляет сам, и без
# них граф не строится.
filters="scale,fps,format,split,palettegen,paletteuse,crop,null,anull,aresample,aformat,setpts,copy,transpose"

# Устройства захвата — единственное, что различается по платформам. Имя
# компонента в configure и имя устройства в командной строке совпадают не
# всегда: `-f x11grab` обеспечивает компонент `xcbgrab`. Поэтому список
# проверки ведётся отдельно, именами, которыми устройства зовут в команде.
extra=()
case "$platform" in
    linux)
        indevs="xcbgrab,alsa"
        devices="x11grab alsa"
        # автоопределение библиотек выключено, значит зависимости устройств
        # перечисляются руками: xcb — это x11grab, alsa — звук
        extra+=(--enable-libxcb --enable-alsa)
        ;;
    macos)
        indevs="avfoundation"
        devices="avfoundation"
        extra+=(--enable-avfoundation --enable-appkit --enable-coreimage)
        ;;
    windows)
        indevs="gdigrab,dshow"
        devices="gdigrab dshow"
        # без -static рядом с ffmpeg.exe пришлось бы класть dll mingw-рантайма
        ldflags_extra="-static"
        ;;
esac

# --- сборка ---------------------------------------------------------------

mkdir -p "$work" "$prefix" "$out"

fetch() {
    local url="$1" archive="$2"
    if [ ! -f "$work/$archive" ]; then
        echo "== качаю $archive"
        curl -fsSL --retry 3 -o "$work/$archive.part" "$url"
        mv "$work/$archive.part" "$work/$archive"
    fi
}

echo "== x264 $X264_VERSION"
fetch "https://code.videolan.org/videolan/x264/-/archive/$X264_VERSION/x264-$X264_VERSION.tar.bz2" \
    "x264.tar.bz2"
if [ ! -d "$work/x264" ]; then
    mkdir -p "$work/x264"
    tar -xf "$work/x264.tar.bz2" -C "$work/x264" --strip-components=1
fi
if [ ! -f "$prefix/lib/libx264.a" ]; then
    (
        cd "$work/x264"
        # только библиотека: консольный x264 нам не нужен, а cli тянет лишнее
        ./configure --prefix="$prefix" --enable-static --enable-pic \
            --disable-cli --disable-opencl ${ASM_FLAGS:-}
        make -j"$jobs"
        make install
    )
fi

echo "== ffmpeg $FFMPEG_VERSION"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "ffmpeg.tar.xz"
if [ ! -d "$work/ffmpeg" ]; then
    mkdir -p "$work/ffmpeg"
    tar -xf "$work/ffmpeg.tar.xz" -C "$work/ffmpeg" --strip-components=1
fi

(
    cd "$work/ffmpeg"
    PKG_CONFIG_PATH="$prefix/lib/pkgconfig" ./configure \
        --prefix="$prefix" \
        --pkg-config-flags=--static \
        --extra-cflags="-I$prefix/include" \
        --extra-ldflags="-L$prefix/lib ${ldflags_extra:-}" \
        --disable-everything \
        --disable-autodetect \
        --disable-doc \
        --disable-ffplay \
        --disable-ffprobe \
        --disable-network \
        --disable-debug \
        --disable-shared \
        --enable-static \
        --enable-gpl \
        --enable-libx264 \
        --enable-encoder="$encoders" \
        --enable-decoder="$decoders" \
        --enable-muxer="$muxers" \
        --enable-demuxer="$demuxers" \
        --enable-parser="$parsers" \
        --enable-protocol="$protocols" \
        --enable-filter="$filters" \
        --enable-indev="$indevs" \
        "${extra[@]}" \
        ${CONFIGURE_EXTRA:-}
    make -j"$jobs"
)

cp "$work/ffmpeg/ffmpeg$exe" "$out/ffmpeg$exe"
strip "$out/ffmpeg$exe" 2>/dev/null || true

# --- проверка собранного --------------------------------------------------
#
# Список возможностей задаётся при сборке, поэтому опечатка в нём не всплывёт
# ни на компиляции, ни на тестах — только у пользователя посреди записи.
missing=()
have() {
    "$out/ffmpeg$exe" -hide_banner -loglevel error "$@" 2>/dev/null
}
check() {
    local kind="$1" name="$2" listing="$3"
    if ! have "-$listing" | grep -q "[[:space:]]$name[[:space:]]"; then
        missing+=("$kind $name")
    fi
}

check кодировщик libx264 encoders
check кодировщик gif encoders
check кодировщик aac encoders
check декодер h264 decoders
check мультиплексор mp4 muxers
check мультиплексор gif muxers
for name in scale fps split palettegen paletteuse crop; do
    check фильтр "$name" filters
done
for name in $devices; do
    if ! have -devices | grep -q "[[:space:]]$name[[:space:]]"; then
        missing+=("устройство $name")
    fi
done

if [ "${#missing[@]}" -ne 0 ]; then
    echo "в собранном ffmpeg нет:" >&2
    printf '  %s\n' "${missing[@]}" >&2
    exit 1
fi

size="$(du -h "$out/ffmpeg$exe" | cut -f1)"
echo
echo "готов: $out/ffmpeg$exe ($size)"
