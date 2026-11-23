"""Nickname cleaning, clan-tag stripping, console marker handling, and canonical keys."""

from __future__ import annotations

import re
from wt_xvm.models import PlatformHint, PlayerIdentity

# Explicit War Thunder squadron decorator pairs (open, close)
_KNOWN_CLAN_PAIRS: set[tuple[str, str]] = {
    ("=", "="),
    ("[", "]"),
    ("-", "-"),
    ("^", "^"),
    ("╖", "╖"),
    ("┾", "┿"),
    ("┺", "┻"),
    ("*", "*"),
    ("_", "_"),
    ("┃", "┃"),
    ("⎛", "⎠"),
    ("⎝", "⎠"),
    ("•", "•"),
    ("✪", "✪"),
    ("╈", "╈"),
    ("╬", "╬"),
    ("╠", "╣"),
    ("┤", "├"),
    ("▄", "▄"),
    ("▀", "▀"),
    ("│", "│"),
    ("║", "║"),
    ("【", "】"),
    ("《", "》"),
    ("〈", "〉"),
    ("«", "»"),
    ("{", "}"),
    ("(", ")"),
    ("<", ">"),
    ("~", "~"),
    ("+", "+"),
    ("#", "#"),
    (".", "."),
}

_CLAN_BRACKET_CHARS = {ch for pair in _KNOWN_CLAN_PAIRS for ch in pair} | {"|", '"', "'"}

_CONSOLE_PREFIXES = ("⋇", "*")
_SPACE_RE = re.compile(r"\s+")
_PLATFORM_SUFFIX_RE = re.compile(r"^(.*?)(@(psn|live))$", re.IGNORECASE)
_CANDIDATE_CLAN_RE = re.compile(r"^((?:[^\w\s]|_))([^\s]{1,8})((?:[^\w\s]|_))\s+(.+)$")
_COMPACT_CLAN_RE = re.compile(
    r"^(?:(=)([^\s=]{2,6})(=)|(\[)([^\s\]]{2,6})(\])|(\^)([^\s\^]{2,6})(\^)|(-)([A-Z0-9_]{2,6})(-))([A-Za-zА-Яа-яЁё0-9_\-].+)$"
)
_CLAN_ONLY_WRAPPED_RE = re.compile(
    r"^(=([^\s=]{1,7})=|\[([^\s\]]{1,7})\]|\^([^\s\^]{1,7})\^)(?:[^\w\s]?[A-Za-z0-9]{1,2}|[^\w\s]{1,2})?$"
)
_CLAN_ONLY_PARTIAL_RE = re.compile(r"^(?:[=\[\^]([A-Za-z0-9_]{1,7})|([A-Za-z0-9_]{2,7})[=\]\^])$")


def is_clan_only_fragment(raw: str) -> bool:
    """Return True if raw OCR text is only a squadron clan-tag fragment (missing CJK nick)."""
    text = _SPACE_RE.sub(" ", (raw or "").strip())
    if not text or " " in text:
        return False
    m = _CLAN_ONLY_WRAPPED_RE.match(text)
    if m:
        body = m.group(2) or m.group(3) or m.group(4) or ""
        return any(ch.isalnum() for ch in body)
    m_partial = _CLAN_ONLY_PARTIAL_RE.match(text)
    if m_partial:
        body = m_partial.group(1) or m_partial.group(2) or ""
        return any(ch.isalnum() for ch in body)
    return False


def _is_clan_decorator_char(ch: str) -> bool:
    """Return True if ch is a known squadron decorator or Unicode box-drawing/dash symbol."""
    if ch in _CLAN_BRACKET_CHARS or ch in ("\u00b7", "\u2022"):
        return True
    code = ord(ch)
    # Box Drawing (2500-257F), Block Elements (2580-259F), Geometric Shapes (25A0-25FF),
    # Misc Symbols (2600-26FF), Dingbats (2700-27BF)
    if 0x2500 <= code <= 0x27BF:
        return True
    # Hyphens / dashes (‐, ‑, ‒, –, —, ―)
    if 0x2010 <= code <= 0x2015:
        return True
    return False


def _is_clan_token(open_ch: str, body: str, close_ch: str) -> bool:
    """Return True if (open_ch, body, close_ch) matches a known WT squadron decorator."""
    if not body or not any(ch.isalnum() for ch in body):
        return False
    if (open_ch, close_ch) in _KNOWN_CLAN_PAIRS:
        return True
    # Allow symmetric non-alphanumeric symbol wrappers not listed explicitly
    if open_ch == close_ch and not open_ch.isalnum() and open_ch != "@":
        return True
    # Allow OCR-mangled or Unicode asymmetric bracket wrappers for typical short clan tags
    if (
        len(body) <= 6
        and _is_clan_decorator_char(open_ch)
        and _is_clan_decorator_char(close_ch)
    ):
        return True
    return False


def make_canonical_key(nick: str) -> str:
    """Build case-insensitive whitespace-normalized key for player deduplication."""
    return _SPACE_RE.sub(" ", nick.strip()).casefold()


def parse_player_identity(raw: str) -> PlayerIdentity:
    """Parse a raw log/chat/OCR player name into clean nick, clan tag, and canonical key.

    Strips AT MOST ONE leading clan tag only when followed by whitespace and a non-empty nick.
    Preserves internal spaces, console markers metadata, and platform hints.
    """
    text = _SPACE_RE.sub(" ", (raw or "").strip())
    if not text:
        return PlayerIdentity(raw="", clean_nick="", canonical_key="")

    clan_tag: str | None = None
    rest = text

    match = _CANDIDATE_CLAN_RE.match(text)
    if match:
        open_ch, body, close_ch, remainder = match.groups()
        if _is_clan_token(open_ch, body, close_ch) and remainder.strip():
            clan_tag = f"{open_ch}{body}{close_ch}"
            rest = remainder.strip()
    else:
        compact = _COMPACT_CLAN_RE.match(text)
        if compact:
            groups = compact.groups()
            remainder = groups[-1]
            for idx in (0, 3, 6, 9):
                if groups[idx] is not None:
                    open_ch, body, close_ch = groups[idx], groups[idx + 1], groups[idx + 2]
                    if any(ch.isalnum() for ch in body):
                        clan_tag = f"{open_ch}{body}{close_ch}"
                        rest = remainder.strip()
                    break

    console_prefix: str | None = None
    platform_hint: PlatformHint = None

    if rest and rest[0] in _CONSOLE_PREFIXES and len(rest) > 1:
        console_prefix = rest[0]
        rest = rest[1:].strip()
        platform_hint = "console"

    plat_match = _PLATFORM_SUFFIX_RE.match(rest)
    if plat_match and plat_match.group(1).strip():
        rest = plat_match.group(1).strip()
        suffix = plat_match.group(3).lower()
        platform_hint = "psn" if suffix == "psn" else "live"

    clean_nick = rest
    canonical_key = make_canonical_key(clean_nick)
    return PlayerIdentity(
        raw=raw,
        clan_tag=clan_tag,
        clean_nick=clean_nick,
        console_prefix=console_prefix,
        platform_hint=platform_hint,
        canonical_key=canonical_key,
    )
