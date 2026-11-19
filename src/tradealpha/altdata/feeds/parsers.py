"""
Tolerant parsers for the news & filings archive.

Design rules
* Raw-first: every record keeps its complete source dict (``Item.raw``); mapped fields are conveniences.
* Field mapping is data (``SourceSpec.mapping``): lists of candidate field names, first non-empty wins (``title`` joins
  all non-empty candidates). Unknown or renamed fields therefore never lose data - they stay in ``raw``.
* Timestamps: many Indian-exchange formats are accepted; naive times are IST (UTC+05:30); stored as epoch seconds.
* XML is parsed with the standard library (expat >= 2.4.1 has billion-laughs protection, no external entities);
  response size is capped by the HTTP client.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

IST = timezone(timedelta(hours=5, minutes=30), "IST")
MAX_BODY_CHARS = 20_000


class ParseError(ValueError):
    pass


@dataclass
class Item:
    external_id: str
    raw: dict
    title: str | None = None
    body: str | None = None
    category: str | None = None
    symbol: str | None = None
    isin: str | None = None
    bse_code: str | None = None
    company: str | None = None
    url: str | None = None
    attachment_url: str | None = None
    published_at: int | None = None
    event_date: str | None = None
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------- time
_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%B-%Y %H:%M:%S", "%d-%B-%Y",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
            "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y%m%d",
            "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %b %Y", "%b %d, %Y %H:%M:%S", "%b %d, %Y", "%d %B %Y",
            "%B %d, %Y")
_FRACTION = re.compile(r"(\d{2}:\d{2}:\d{2})\.\d+")
_SPACES = re.compile(r"\s+")


def _valid(dt: datetime) -> bool:
    return 1990 <= dt.year <= 2100


def parse_time(value) -> int | None:
    """Epoch seconds (UTC) from the formats Indian exchanges, RSS and GDELT use. Naive values are IST."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        v = float(value)
        v = v / 1000 if v > 1e11 else v
        return int(v) if 631152000 <= v <= 4102444800 else None      # 1990 .. 2100
    s = _SPACES.sub(" ", str(value).strip())
    if not s or s in ("-", "NA", "N/A", "null", "None"):
        return None
    dt = None
    if re.search(r"[+-]\d{4}$|GMT|UTC|[A-Z]{3}$", s) and "," in s[:5]:       # RFC 822 (RSS pubDate)
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            dt = None
    if dt is None:
        iso = _FRACTION.sub(r"\1", s)
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")) if re.match(r"\d{4}-\d{2}-\d{2}", iso) else None
        except ValueError:
            dt = None
        if dt is None:
            for fmt in _FORMATS:
                try:
                    dt = datetime.strptime(iso, fmt)
                    if fmt.endswith("Z"):
                        dt = dt.replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
    if dt is None:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp()) if _valid(dt) else None


def to_iso_date(value) -> str | None:
    ts = parse_time(value)
    return datetime.fromtimestamp(ts, IST).date().isoformat() if ts is not None else None


# ---------------------------------------------------------------------------------------------- helpers
def canonical(raw) -> bytes:
    return json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def content_hash(raw) -> str:
    return hashlib.sha256(canonical(raw)).hexdigest()[:32]


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return str(v)
    s = str(v).strip()
    return s or None


def first(rec: dict, keys) -> str | None:
    for k in keys or ():
        v = _text(rec.get(k))
        if v is not None and v not in ("-", "NA", "null"):
            return v
    return None


def strip_html(s: str | None) -> str | None:
    if not s:
        return s
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return _SPACES.sub(" ", html.unescape(s)).strip() or None


def map_record(rec: dict, mapping: dict) -> Item:
    """Build an Item from one source record using the candidate-field mapping."""
    id_keys = mapping.get("id")
    parts = [first(rec, [k]) for k in id_keys] if id_keys else []
    parts = [p for p in parts if p]
    ext = "|".join(parts) if parts else "h:" + content_hash(rec)
    title_parts = [first(rec, [k]) for k in mapping.get("title", ())]
    title = " · ".join(dict.fromkeys(p for p in title_parts if p)) or None
    ts = None
    for k in mapping.get("time", ()):
        ts = parse_time(rec.get(k))
        if ts is not None:
            break
    att = first(rec, mapping.get("attachment"))
    if att and mapping.get("attachment_prefix") and not att.lower().startswith(("http://", "https://")):
        att = mapping["attachment_prefix"] + att.lstrip("/")
    if att and not att.lower().startswith(("http://", "https://")):
        att = None
    body = first(rec, mapping.get("body"))
    ev = first(rec, mapping.get("event_date"))
    symbol = first(rec, mapping.get("symbol"))
    return Item(external_id=ext[:300], raw=rec, title=title[:1000] if title else None,
                body=strip_html(body)[:MAX_BODY_CHARS] if body else None,
                category=(first(rec, mapping.get("category")) or None), symbol=symbol.upper() if symbol else None,
                isin=first(rec, mapping.get("isin")), bse_code=first(rec, mapping.get("bse_code")),
                company=first(rec, mapping.get("company")), url=first(rec, mapping.get("url")), attachment_url=att,
                published_at=ts, event_date=to_iso_date(ev) if ev else None)


def _records(payload, list_keys) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for k in list(list_keys or ()) + ["data", "Table", "rows", "items", "records"]:
            v = payload.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return [dict(v, _key=k) for k, v in payload.items()]
        return []
    raise ParseError(f"unexpected JSON payload type {type(payload).__name__}")


def _json(body: bytes):
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        snippet = body[:80].decode("utf-8", "replace")
        raise ParseError(f"not JSON ({e.__class__.__name__}): {snippet!r}") from e


# ---------------------------------------------------------------------------------------------- formats
def parse_json(body: bytes, mapping: dict) -> tuple[list[Item], dict]:
    """JSON list / {data: [...]} / BSE {Table: [...], Table1: [{ROWCNT}]} -> items. Returns (items, meta)."""
    payload = _json(body)
    recs = _records(payload, mapping.get("list"))
    meta = {}
    if isinstance(payload, dict):
        t1 = payload.get("Table1")
        if isinstance(t1, list) and t1 and isinstance(t1[0], dict) and "ROWCNT" in t1[0]:
            try:
                meta["total_rows"] = int(t1[0]["ROWCNT"])
            except (TypeError, ValueError):
                pass
    return [map_record(r, mapping) for r in recs], meta


_ATOM = "{http://www.w3.org/2005/Atom}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


def _el_dict(el) -> dict:
    out = {}
    for ch in el:
        tag = ch.tag.split("}")[-1]
        if tag == "link" and ch.get("href"):
            out.setdefault("link", ch.get("href"))
            continue
        val = (ch.text or "").strip()
        if not val and ch.attrib:
            val = dict(ch.attrib)
        if tag in out:
            prev = out[tag]
            out[tag] = (prev if isinstance(prev, list) else [prev]) + [val]
        else:
            out[tag] = val
    return out


def parse_rss(body: bytes, mapping: dict | None = None) -> tuple[list[Item], dict]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as e:
        raise ParseError(f"not XML: {e}") from e
    entries = root.findall("./channel/item") or root.findall(".//item") or root.findall(f".//{_ATOM}entry")
    if not entries and root.tag.split("}")[-1] not in ("rss", "feed", "RDF"):
        raise ParseError(f"not an RSS/Atom document (root <{root.tag}>)")
    items = []
    for el in entries:
        rec = _el_dict(el)
        link = rec.get("link") if isinstance(rec.get("link"), str) else None
        guid = rec.get("guid") or rec.get("id")
        guid = guid if isinstance(guid, str) and guid else None
        title = strip_html(rec.get("title") if isinstance(rec.get("title"), str) else None)
        desc = rec.get("encoded") or rec.get("description") or rec.get("summary") or rec.get("content")
        desc = strip_html(desc if isinstance(desc, str) else None)
        ts = None
        for k in ("pubDate", "published", "updated", "date", "issued"):
            v = rec.get(k)
            if isinstance(v, str):
                ts = parse_time(v)
                if ts is not None:
                    break
        cat = rec.get("category")
        cat = cat[0] if isinstance(cat, list) and cat and isinstance(cat[0], str) else cat if isinstance(cat, str) else None
        ext = guid or link or ("h:" + content_hash(rec))
        items.append(Item(external_id=ext[:300], raw=rec, title=title[:1000] if title else None,
                          body=desc[:MAX_BODY_CHARS] if desc else None, category=cat, url=link, published_at=ts))
    return items, {}


def parse_gdelt(body: bytes, mapping: dict | None = None) -> tuple[list[Item], dict]:
    text = body.decode("utf-8-sig", "replace").strip()
    if not text or text in ("{}", "[]"):
        return [], {}
    payload = _json(body)
    arts = payload.get("articles", []) if isinstance(payload, dict) else []
    items = []
    for a in arts:
        if not isinstance(a, dict) or not a.get("url"):
            continue
        items.append(Item(external_id=str(a["url"])[:300], raw=a, title=_text(a.get("title")),
                          category=_text(a.get("domain")), url=a.get("url"), published_at=parse_time(a.get("seendate")),
                          extra={"language": a.get("language"), "sourcecountry": a.get("sourcecountry")}))
    return items, {}


def parse_csv(body: bytes, mapping: dict) -> tuple[list[Item], dict]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = body.decode("latin-1")
    lines = text.splitlines()
    if not lines:
        return [], {}
    start = 0
    marker = mapping.get("header_contains")
    if marker:
        start = next((i for i, ln in enumerate(lines) if marker.lower() in ln.lower()), 0)
    if "<html" in text[:500].lower():
        raise ParseError("got HTML instead of CSV")
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    items = []
    for row in reader:
        rec = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
        if not any(v for v in rec.values()):
            continue
        if mapping.get("skip_if_contains") and any(mapping["skip_if_contains"] in str(v) for v in rec.values()):
            continue
        items.append(map_record(rec, mapping))
    return items, {}


PARSERS = {"json": parse_json, "rss": parse_rss, "gdelt": parse_gdelt, "csv": parse_csv}


def parse(fmt: str, body: bytes, mapping: dict) -> tuple[list[Item], dict]:
    if fmt not in PARSERS:
        raise ParseError(f"unknown format {fmt}")
    items, meta = PARSERS[fmt](body, mapping or {})
    seen, out = set(), []
    for it in items:                       # de-duplicate inside one response (first occurrence wins)
        if it.external_id in seen:
            continue
        seen.add(it.external_id)
        out.append(it)
    return out, meta
