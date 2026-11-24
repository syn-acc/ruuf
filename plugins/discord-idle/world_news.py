"""Bounded, deterministic public RSS intake for the idle writer."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPException
import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


FEEDS = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("NASA", "https://www.nasa.gov/news-release/feed/"),
)
MAX_BODY = 512 * 1024
# Leave room for the surrounding sources_checked/sources_available envelope.
MAX_OUTPUT = 6300
MAX_ITEMS = 8

INTERESTS = {
    "ai_machines_autonomy": r"\b(?:ai|artificial intelligence|machine learning|robot|robotics|autonom(?:y|ous)|semiconductor|nvidia|quantum computing)\b",
    "biology_evolution_minds": r"\b(?:biology|biological|evolution|evolutionary|brain|brains|mind|minds|consciousness|neuron(?:s|al)?|genome|gene editing|organoid|synthetic cell|animal cognition)\b",
    "space_posthuman_futures": r"\b(?:space|planet|planets|mars|moon|lunar|asteroid|astronaut|cosmos|star|stars|telescope|webb|galaxy|galaxies|posthuman|transhuman|future of humanity)\b",
    "infrastructure_control_power": r"\b(?:infrastructure|power grid|energy grid|network|data center|internet|surveillance|control|institution|institutions|government|regulation|monopoly|centralization)\b",
    "archives_obsolescence_digital_culture": r"\b(?:archive|archives|obsolete|obsolescence|digital culture|digitization|preservation|software|computing|online culture)\b",
}
SYSTEMIC = re.compile(r"\b(?:global|worldwide|international|planetary|systemic|crisis|treaty|policy|public health|climate|election|migration|trade|economy)\b", re.I)
TAG = re.compile(r"\b(?:item|channel|rss|atom|content|summary|description|encoded|dc|media):", re.I)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _clean(value, limit):
    parser = _Text()
    try:
        parser.feed(str(value or ""))
        parser.close()
        value = " ".join(parser.parts)
    except Exception:
        value = str(value or "")
    value = "".join(c if c == "\t" or c in "\n\r" or ord(c) >= 32 and ord(c) != 127 else " " for c in value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child(node, names):
    for child in node.iter():
        if child is not node and _local(child.tag) in names:
            return "".join(child.itertext()).strip()
    return ""


def _date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _item_url(node):
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        value = (child.text or "").strip()
        if child.attrib.get("rel", "alternate") in ("alternate", ""):
            return href or value
    return ""


def _interests(text):
    return [name for name, pattern in INTERESTS.items() if re.search(pattern, text, re.I)]


def parse_feed(data, source, now):
    """Parse one bounded RSS/Atom document, returning only fresh, valid articles."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BODY:
        return []
    # ElementTree does not resolve external entities; reject declarations outright.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.I):
        return []
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        return []
    result = []
    low, high = now.astimezone(timezone.utc) - timedelta(hours=48), now.astimezone(timezone.utc) + timedelta(minutes=10)
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = _clean(_child(node, {"title"}), 180)
        summary = _clean(_child(node, {"description", "summary", "encoded", "content"}), 450)
        published = None
        for child in node.iter():
            if _local(child.tag) in {"pubdate", "published", "updated", "date", "issued", "created"}:
                published = _date("".join(child.itertext()))
                if published:
                    break
        url = _item_url(node)
        if not url:
            url = _child(node, {"guid", "id"})
        from urllib.parse import urlsplit
        try:
            parsed_url = urlsplit(url)
        except ValueError:
            continue
        host = (parsed_url.hostname or "").lower()
        domains = ("bbc.com", "bbc.co.uk", "arstechnica.com", "nasa.gov")
        if (not title or not published or published < low or published > high
                or parsed_url.scheme != "https" or parsed_url.username or parsed_url.password
                or not any(host == d or host.endswith("." + d) for d in domains)
                or any(ord(c) <= 32 or c in "<>" for c in url) or len(url) > 1000):
            continue
        interests = _interests(title + " " + summary)
        canonical = url.split("#", 1)[0]
        ident = hashlib.sha256((source + "\0" + canonical).encode()).hexdigest()[:24]
        result.append({"id": ident, "source": source, "title": title,
                       "summary": summary, "published_at": published.isoformat().replace("+00:00", "Z"),
                       "url": url, "interests": interests})
    return result


def rank_items(items):
    """Dedupe and rank topical articles before applying the outbound size cap."""
    unique = {}
    for item in items:
        key = item["url"].split("#", 1)[0].rstrip("/").lower()
        if key not in unique:
            unique[key] = item
    def score(item):
        text = item["title"] + " " + item["summary"]
        return (len(item["interests"]) * 4 + (2 if SYSTEMIC.search(text) else 0), item["published_at"])
    ordered = sorted(unique.values(), key=score, reverse=True)
    chosen = []
    for item in ordered:
        if len(chosen) >= MAX_ITEMS:
            break
        candidate = chosen + [item]
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > MAX_OUTPUT:
            continue
        chosen.append(item)
    return chosen


class _NoOffHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        if urlsplit(newurl).hostname not in self.allowed or urlsplit(newurl).scheme != "https":
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url, allowed):
    from urllib.parse import urlsplit
    opener = urllib.request.build_opener(_NoOffHostRedirect(allowed))
    request = urllib.request.Request(url, headers={"User-Agent": "HermesWorldNews/1.0", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    with opener.open(request, timeout=4) as response:
        if urlsplit(response.geturl()).hostname not in allowed:
            raise ValueError("redirected outside feed host allowlist")
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError("feed body too large")
        return data


def collect_news(now, exclude_ids=()):
    """Fetch the fixed public feeds sequentially; failed sources are skipped."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    from urllib.parse import urlsplit
    allowed = {urlsplit(url).hostname for _, url in FEEDS}
    items, available = [], 0
    for source, url in FEEDS:
        try:
            data = _fetch(url, allowed)
            available += 1
            items.extend(parse_feed(data, source, now))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, HTTPException):
            continue
    seen = set(exclude_ids)
    return {"items": rank_items([item for item in items if item["id"] not in seen]),
            "sources_checked": len(FEEDS), "sources_available": available}
