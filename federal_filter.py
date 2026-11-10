"""Editorial rules for federal reposts. No I/O or Telegram client imports."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

INVISIBLE = dict.fromkeys(map(ord, "\u200b\ufeff"))
DASHES = str.maketrans({char: "-" for char in "‐‑‒–—―−"})
KNOWN_SOURCES = {"vrv_radar", "vrv_support", "locatorru"}
URL = re.compile(
    r"(?<![\w@])(?:https?://|www\.|(?:t\.me|telegram\.me|(?:max|vk|rutube)\.ru|vk\.com|dzen\.ru)(?=/|\b))[^\s<>()]*",
    re.I,
)
MENTION = re.compile(r"(?<!\w)@[a-z0-9_]+", re.I | re.ASCII)
DECORATION = " \t\n👉👇🔗➡➜➤🔹▪️•🔴🟡🟢⚪❗⚡🚨📢🗺📍📌|-/\\:;,.!?!()[]{}«»*#_"
SOCIAL = r"(?:макс|мах|max|вк|vk|дзен|dzen|rutube|рутуб)\b"
SIGNATURE = re.compile(
    r"^(?:подпис(?:аться|ывайтесь|ывайся)|подпиш(?:ись|итесь))\b"
    r"|^прислать\s+новость\b|^источник\s*:|^наш\s+(?:телеграм[- ]?)?канал\s*:"
    rf"|^курс\s+на\s+{SOCIAL}|^(?:(?:наш|новый)\s+)?(?:канал|мы)\s+(?:в|на)\s+{SOCIAL}"
    rf"|^{SOCIAL}(?=\s*[:!]|\s*$)",
    re.I,
)
ADS = re.compile(
    r"\b(?:erid|инн|огрн(?:ип)?|ооо|ип|реклам(?:а|ы|у|е|ой|н\w*)|акци[яию]|сбп|yoomoney|юmoney|qiwi|тинькофф)\b"
    r"|\b(?:промокод|скидк|распродаж|казино|casino|букмекер|ставк|фрибет|слот|рулетк|"
    r"крипт|арбитраж|биржа|заработок|реквизит|донат|пожертвован|розыгрыш)\w*"
    r"|\b(?:партнер\s+канала|при\s+поддержке|доход\s+от|сбор\s+средств|открыт\s+сбор|"
    r"на\s+нужды|карта\s+сбер|т\s*-\s*банк)"
    r"|\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"
)

CIRCLES = "🔴🟡🟢⚪"
LEADING_MARKERS = re.compile(r"^(?:[\s\uFE0F🔴🟡🟢⚪❗⚡🚨📢‼❕🔔⚠]+)")
ALL_CLEAR = re.compile(
    r"\bотбой\b|\b(?:опасност[ьи]|угроз[аы]|тревог[аи])\b[^.!?\n]{0,55}"
    r"\b(?:отмен[её]н[аы]?|снят[аы]?|миновал[аи]?)\b"
)
NEGATED_CLEAR = re.compile(
    r"\b(?:без\s+отбоя|нет\s+отбоя|отбоя\s+нет|не\s+(?:было\s+)?объявлен\w*\s+отбой|не\s+отбой)\b"
    r"|\bотбой\b[^,.!?;\n]{0,55}\b(?:не\s+(?:объяв|поступ|был|дан|ожида|подтверж)\w*|нет|ожидается)\b"
    r"|\bне\s+(?:отмен[её]н|снят|миновал)\w*\b"
)
REPORT_LABEL = re.compile(r"\b(?:итоги|сводка|отчет)\b")
HISTORICAL = re.compile(
    r"\bза\s+(?:(?:прошедшую|минувшую|прошедшие|минувшие)\s+)?(?:ночь|сутки)\b|"
    r"\b(?:последствия|в\s+результате)\s+(?:ночной\s+)?(?:атаки|обстрела|падения)\b"
)
RED = re.compile(
    r"\b(?:пво|баллистик\w*|обстрел\w*|сбит\w*|сбил\w*|сбива\w*|fpv)\b|"
    r"\b(?:ракетн\w*|авиационн\w*)\s+(?:опасност\w*|угроз\w*)|"
    r"\bопасност\w*\s+(?:по|из-за)\s+(?:обломк\w*|осколк\w*)"
)
UAV_ACTION = re.compile(
    r"\b(?:фиксац\w*|фиксиру\w*|зафиксир\w*|пролет\w*|летит|летят|курс\w*|"
    r"атак\w*|замечен\w*|наблюда\w*)\b"
)
UAV = re.compile(r"\b(?:бпла|дрон\w*|беспилот\w*|fpv)\b")
IMMEDIATE = re.compile(
    r"\b(?:прямо\s+сейчас|в\s+данный\s+момент|продолжается|продолжают|"
    r"работа(?:ет|ют)\s+пво|пво\s+работа(?:ет|ют)|сбива\w*|фиксиру\w*|летят|летит)\b"
)
WARNING = re.compile(r"\b(?:опасность|внимание)\s+(?:по\s+)?(?:бпла|беспилот\w*)\b")
NEWS_PREFIX = re.compile(r"^(?:постфактум|сводка|инфо)\s*:\s*", re.I)
THREAT_MAP = re.compile(
    r"^(?:(?:актуальн\w*|обновл[её]нн\w*|интерактивн\w*)\s+)?карта\s+"
    r"(?:(?:всех|активн\w*|текущ\w*|действующ\w*)\s+)*(?:угроз|опасност)\w*\b",
    re.I,
)
SAFETY_MEASURES = re.compile(r"\bмер(?:ы|ам|ами|ах)?\s+безопасности\b", re.I)


def normalize(text: str) -> str:
    return text.translate(INVISIBLE).translate(DASHES)


def entity_links(raw: str, entities) -> list[tuple[int, int, str]]:
    # Telegram counts UTF-16 code units. Resolve positions before touching the text.
    encoded = raw.encode("utf-16-le")
    links = []
    for entity in entities or ():
        kind = str(getattr(entity.type, "name", entity.type)).lower()
        if kind not in {"url", "text_link"} or entity.length <= 0:
            continue
        start = entity.offset * 2
        end = start + entity.length * 2
        offset = len(encoded[:start].decode("utf-16-le", errors="ignore"))
        label = encoded[start:end].decode("utf-16-le", errors="ignore")
        url = (getattr(entity, "url", None) or "") if kind == "text_link" else label
        links.append((offset, offset + len(label), url))
    return links


def remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    parts = []
    cursor = 0
    for start, end in sorted(spans):
        if start > cursor:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    parts.append(text[cursor:])
    return "".join(parts)


def is_source_link(url: str, source: str) -> bool:
    url = normalize(url).strip().rstrip(".,!?;:")
    try:
        parsed = urlsplit(url if "://" in url else f"https://{url}")
        handle = parsed.path.strip("/").removeprefix("s/").split("/", 1)[0].casefold()
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.casefold().removeprefix("www.") in {"t.me", "telegram.me"}
            and handle in KNOWN_SOURCES | {source.lstrip("@").casefold()}
        )
    except ValueError:
        return False


def clean_post(raw: str, entities=(), source: str = "vrv_radar") -> str:
    if not raw:
        return ""
    headline = " ".join(normalize(raw).split()).lstrip(DECORATION)
    if THREAT_MAP.match(NEWS_PREFIX.sub("", headline).lstrip(DECORATION)):
        return ""
    links = entity_links(raw, entities)
    lines = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        end = offset + len(content)
        line_links = [
            (max(start, offset) - offset, min(stop, end) - offset, url)
            for start, stop, url in links if start < end and stop > offset
        ]
        lines.append((content, line_links))
        offset += len(line)

    # Strip only the trailing signature block. Inline links in retained lines are vetted below.
    while lines:
        line, line_links = lines[-1]
        visible = normalize(line).strip(DECORATION)
        unlinked = normalize(remove_spans(line, [(start, end) for start, end, _ in line_links]))
        remainder = MENTION.sub("", URL.sub("", unlinked)).strip(DECORATION)
        separated_link = (
            line_links and len(lines) >= 2 and not normalize(lines[-2][0]).strip()
            and classify_status(visible) == "⚪"
            and not (HISTORICAL.search(visible.casefold()) or REPORT_LABEL.search(visible.casefold()))
        )
        if not visible or not remainder or SIGNATURE.match(visible) or SIGNATURE.match(remainder) or separated_link:
            lines.pop()
        else:
            break

    body_lines = []
    for line, line_links in lines:
        if any(not is_source_link(url, source) for _, _, url in line_links):
            return ""
        line = normalize(remove_spans(line, [(start, end) for start, end, _ in line_links]))
        if any(not is_source_link(match.group(), source) for match in URL.finditer(line)):
            return ""
        if SIGNATURE.match(line.strip(DECORATION)):
            return ""
        cleaned = MENTION.sub("", URL.sub("", line)).strip()
        body_lines.append(re.sub(r"[ \t]{2,}", " ", cleaned))

    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    body = body.rstrip(" \t\n👉👇🔗➡➜➤-|️")
    folded = re.sub(r"\s+", " ", body.casefold().replace("ё", "е"))
    if ADS.search(folded) or not re.search(r"[^\W\d_]{2}", body):
        return ""
    return body


def classify_status(text: str) -> str:
    folded = text.casefold().replace("ё", "е")
    all_clear = [clause for clause in re.split(r"[.!?;\n]+", folded) if ALL_CLEAR.search(clause)]
    if any(not NEGATED_CLEAR.search(clause) for clause in all_clear):
        return "🟢"
    if SAFETY_MEASURES.search(folded):
        return "🔴"
    warning = WARNING.search(folded)
    report = HISTORICAL.search(folded) or (REPORT_LABEL.search(folded) and not warning)
    if report and not IMMEDIATE.search(folded):
        return "⚪"
    if RED.search(folded) or (UAV.search(folded) and UAV_ACTION.search(folded)):
        return "🔴"
    if warning:
        return "🟡"
    return "⚪"


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def format_post(text: str, title: str, url: str, limit: int) -> str:
    markers = LEADING_MARKERS.match(text)
    status = next((char for char in markers[0] if char in CIRCLES), None) if markers else None
    body = text[markers.end():] if markers else text
    body = re.sub(r"[🔴🟡🟢⚪]\uFE0F?|\*\*|__|(?m:^[ \t]*#+[ \t]*)", "", body).strip()
    if status is None or SAFETY_MEASURES.search(body):
        status = classify_status(body)
    body = NEWS_PREFIX.sub("", body).strip()
    if not body:
        return ""
    footer = f"\n\n📡 {html.escape(title)} - <a href='{html.escape(url)}'>Подписаться</a>"
    budget = limit - utf16_length(f"{status} {footer}")

    words = list(re.finditer(r"\S+", body))
    end = sentence_end = used = 0
    for word in words:
        used += utf16_length(html.escape(body[end:word.end()]))
        if used > budget:
            break
        end = word.end()
        if re.search(r"[.!?…][»\")\]]?$", word[0]):
            sentence_end = end
    if not end:
        return ""
    if end < len(body) and sentence_end >= end * 0.8:
        end = sentence_end
    return f"{status} {html.escape(body[:end])}{footer}"
