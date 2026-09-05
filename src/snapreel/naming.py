"""Имена клипов: какой шаблон допустим и как узнать в файле свою запись.

Отдельный модуль, потому что нужен и `config` (проверить шаблон при загрузке),
и `storage` (построить по нему выражение) — импортировать друг друга им нельзя.

Ключевое ограничение: множество директив strftime не перечислимо, и угадывать
принадлежность файла разбором произвольного шаблона нельзя — незнакомая
директива либо совпадёт с чем угодно и унесёт чужие файлы, либо не совпадёт ни
с чем и `keep_days` молча перестанет работать. Поэтому шаблон сужен до
грамматики, которую код действительно умеет разбирать, а всё за её пределами
отвергается при загрузке конфига.
"""

from __future__ import annotations

import re

# директива -> выражение, которым она узнаётся обратно
DIRECTIVES = {
    "%Y": r"\d{4}",
    "%y": r"\d{2}",
    "%m": r"\d{2}",
    "%d": r"\d{2}",
    "%H": r"\d{2}",
    "%I": r"\d{2}",
    "%M": r"\d{2}",
    "%S": r"\d{2}",
    "%j": r"\d{3}",
    "%%": r"%",
}

# символы, из которых можно составлять литеральную часть имени
LITERAL = re.compile(r"[A-Za-z0-9._\- ]")

SUFFIXES = ("mp4", "gif")


class TemplateError(ValueError):
    """Шаблон имени содержит то, что snapreel не сможет узнать обратно."""


def validate(template: str) -> None:
    """Проверяет шаблон целиком. Вызывается при загрузке конфига."""
    _parse(template)


def _parse(template: str) -> list[str]:
    """Разбирает шаблон в куски регулярного выражения."""
    if not template:
        raise TemplateError("filename_template пуст")

    parts: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char == "%":
            token = template[index : index + 2]
            if token not in DIRECTIVES:
                allowed = ", ".join(sorted(DIRECTIVES))
                raise TemplateError(
                    f"filename_template: директива {token!r} не поддерживается. "
                    f"Допустимы {allowed}, буквы, цифры, точка, дефис, подчёркивание и пробел"
                )
            parts.append(DIRECTIVES[token])
            index += 2
            continue
        if not LITERAL.fullmatch(char):
            raise TemplateError(
                f"filename_template: символ {char!r} недопустим в имени файла. "
                "Разрешены буквы, цифры, точка, подчёркивание, дефис и пробел"
            )
        parts.append(re.escape(char))
        index += 1
    return parts


def pattern(template: str) -> re.Pattern[str]:
    """Выражение, узнающее имена, которые породит `storage.new_path`.

    Учитывает суффикс-счётчик `-2`, который добавляется при совпадении имён.
    """
    body = "".join(_parse(template))
    return re.compile(rf"{body}(-\d+)?\.({'|'.join(SUFFIXES)})", re.ASCII)


def is_ours(name: str, template: str) -> bool:
    try:
        return bool(pattern(template).fullmatch(name))
    except TemplateError:
        # непроверенный шаблон трактуем как «ничего не наше»: удалить чужое
        # хуже, чем не удалить своё
        return False
