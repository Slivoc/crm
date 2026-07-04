import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from db import execute as db_execute, db_cursor


GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
REQUEST_TIMEOUT = 10

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "cmpid",
    "ref",
}

SEED_SOURCES = [
    ("HeliHub - All helicopter news", "rss", "https://www.helihub.com/tag/news/feed/", None, "rotary", 5),
    ("HeliHub - Airbus Helicopters", "rss", "https://www.helihub.com/tag/news+airbus-helicopters/feed/rss/", None, "oem", 5),
    ("HeliHub - Leonardo", "rss", "https://www.helihub.com/tag/news+leonardo/feed/rss/", None, "oem", 5),
    ("HeliHub - Offshore", "rss", "https://www.helihub.com/tag/news+offshore/feed/rss/", None, "offshore", 5),
    ("HeliHub - EMS", "rss", "https://www.helihub.com/tag/news+ems/feed/rss/", None, "hems", 5),
    ("HeliHub - Law Enforcement", "rss", "https://www.helihub.com/tag/news+law-enforcement/feed/rss/", None, "law_enforcement", 4),
    ("HeliHub - UK", "rss", "https://www.helihub.com/tag/news+uk/feed/rss/", None, "uk", 4),
    ("Airbus RSS page", "webpage", "https://www.airbus.com/en/rss-feeds", None, "oem", 4),
    ("Leonardo RSS page", "webpage", "https://www.leonardo.com/en/rss", None, "oem", 4),
    ("EASA RSS page", "webpage", "https://www.easa.europa.eu/en/rss", None, "regulator", 4),
    ("NPAS news feed", "rss", "https://www.npas.police.uk/news/feed", None, "law_enforcement", 4),
]

GDELT_QUERIES = [
    '"helicopter" "contract"',
    '"helicopter" "maintenance"',
    '"helicopter" "MRO"',
    '"offshore helicopter"',
    '"search and rescue" helicopter',
    '"HEMS" helicopter',
    '"air ambulance" helicopter',
    '"Airbus Helicopters" H145',
    '"Airbus Helicopters" H135',
    '"Airbus Helicopters" H175',
    '"Leonardo" AW139',
    '"Leonardo" AW189',
    '"Sikorsky" "S-92"',
    '"Bristow" helicopter',
    '"CHC Helicopter"',
    '"NHV" helicopter',
    '"Avincis" helicopter',
    '"ADAC Luftrettung"',
    '"DRF Luftrettung"',
    '"OAMTC" helicopter',
]

PLATFORMS = [
    "H135",
    "H145",
    "H160",
    "H175",
    "H225",
    "AS332",
    "EC135",
    "BK117",
    "AW109",
    "AW119",
    "AW139",
    "AW169",
    "AW189",
    "S-76",
    "S-92",
    "Bell 412",
    "Bell 429",
]

SECTOR_KEYWORDS = {
    "SAR": ["search and rescue", "sar", "coastguard", "rescue"],
    "HEMS": ["hems", "air ambulance", "medical helicopter", "ems"],
    "offshore": ["offshore", "oil and gas", "wind farm", "energy"],
    "MRO": ["mro", "maintenance", "repair", "overhaul", "support contract"],
    "firefighting": ["firefighting", "fire fighting", "aerial firefighting"],
}

NEGATIVE_KEYWORDS = [
    "job advert",
    "job opening",
    "recruitment",
    "sponsor",
    "sponsorship",
    "share price",
    "stock price",
]

AVIATION_KEYWORDS = [
    "helicopter",
    "airbus",
    "leonardo",
    "sikorsky",
    "bell",
    "aviation",
    "air ambulance",
    "offshore",
    "sar",
    "mro",
]


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path or "/")
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS]
    )
    return urlunparse((scheme, netloc, path, "", query, ""))


def source_domain(url):
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def normalize_title(title):
    value = (title or "").lower()
    value = re.sub(r"\s+[-|]\s+[^-|]+$", "", value)
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def digest(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for parser in (
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
        parsedate_to_datetime,
    ):
        try:
            parsed = parser(text)
            return parsed.replace(tzinfo=None) if parsed else None
        except Exception:
            continue
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def ensure_seed_news_sources():
    for name, source_type, url, query, sector_tag, priority in SEED_SOURCES:
        _upsert_source(name, source_type, url=url, query=query, sector_tag=sector_tag, priority=priority)
    for query in GDELT_QUERIES:
        _upsert_source(
            f"GDELT - {query}",
            "gdelt_query",
            url=GDELT_ENDPOINT,
            query=query,
            sector_tag="rotary",
            priority=3,
            check_frequency_minutes=720,
        )


def _upsert_source(name, source_type, url=None, query=None, sector_tag=None, priority=3, check_frequency_minutes=1440):
    db_execute(
        """
        INSERT INTO news_sources
            (name, source_type, url, query, sector_tag, priority, check_frequency_minutes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_type, name) DO UPDATE SET
            url = EXCLUDED.url,
            query = EXCLUDED.query,
            sector_tag = EXCLUDED.sector_tag,
            priority = EXCLUDED.priority,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, source_type, url, query, sector_tag, priority, check_frequency_minutes),
        commit=True,
    )


def due_sources(limit=50, source_type=None):
    params = []
    source_filter = ""
    if source_type:
        source_filter = "AND source_type = ?"
        params.append(source_type)
    params.append(limit)
    return db_execute(
        f"""
        SELECT *
        FROM news_sources
        WHERE active = TRUE
          {source_filter}
          AND (
            last_checked_at IS NULL
            OR last_checked_at <= CURRENT_TIMESTAMP - (check_frequency_minutes * INTERVAL '1 minute')
          )
        ORDER BY priority DESC, COALESCE(last_checked_at, TIMESTAMP '1970-01-01') ASC
        LIMIT ?
        """,
        tuple(params),
        fetch="all",
    ) or []


def list_sources():
    return db_execute(
        """
        SELECT ns.*,
               COUNT(na.id) AS article_count
        FROM news_sources ns
        LEFT JOIN news_articles na ON na.source_id = ns.id
        GROUP BY ns.id
        ORDER BY ns.active DESC, ns.priority DESC, ns.name
        """,
        fetch="all",
    ) or []


def list_recent_articles(limit=100):
    return db_execute(
        """
        SELECT na.*,
               ns.name AS source_config_name,
               COUNT(acm.id) AS customer_match_count
        FROM news_articles na
        LEFT JOIN news_sources ns ON ns.id = na.source_id
        LEFT JOIN article_customer_mentions acm ON acm.article_id = na.id
        WHERE na.duplicate_of_article_id IS NULL
        GROUP BY na.id, ns.name
        ORDER BY COALESCE(na.published_at, na.fetched_at) DESC
        LIMIT ?
        """,
        (limit,),
        fetch="all",
    ) or []


def ingestion_stats():
    row = db_execute(
        """
        SELECT
            (SELECT COUNT(*) FROM news_sources WHERE active = TRUE) AS active_sources,
            (SELECT COUNT(*) FROM news_articles) AS total_articles,
            (SELECT COUNT(*) FROM news_articles WHERE fetched_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS articles_24h,
            (SELECT COUNT(*) FROM article_customer_mentions) AS total_matches,
            (SELECT COUNT(*) FROM article_customer_mentions WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS matches_24h
        """,
        fetch="one",
    )
    return dict(row or {})


def set_source_active(source_id, active):
    db_execute(
        "UPDATE news_sources SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (bool(active), source_id),
        commit=True,
    )


def run_ingestion(source_type=None, limit=50):
    ensure_seed_news_sources()
    sources = due_sources(limit=limit, source_type=source_type)
    customer_aliases = get_customer_aliases()
    result = {
        "sources_checked": 0,
        "articles_seen": 0,
        "articles_inserted": 0,
        "duplicates": 0,
        "customer_matches": 0,
        "platform_matches": 0,
        "errors": [],
    }
    for source in sources:
        result["sources_checked"] += 1
        try:
            articles = fetch_source(dict(source))
            result["articles_seen"] += len(articles)
            source_inserted = 0
            source_duplicates = 0
            for article in articles:
                article_id, duplicate = upsert_article(article)
                if not article_id:
                    continue
                if duplicate:
                    source_duplicates += 1
                else:
                    source_inserted += 1
                result["customer_matches"] += match_article_to_customers(article_id, aliases=customer_aliases)
                result["platform_matches"] += match_article_platforms(article_id)
            result["articles_inserted"] += source_inserted
            result["duplicates"] += source_duplicates
            _mark_source_success(source["id"])
        except Exception as exc:
            result["errors"].append({"source": source.get("name"), "error": str(exc)})
            _mark_source_error(source["id"], str(exc))
    return result


def fetch_source(source):
    source_type = source.get("source_type")
    if source_type in {"rss", "company_newsroom"}:
        return fetch_rss_source(source)
    if source_type == "webpage":
        return discover_webpage_feeds(source)
    if source_type == "gdelt_query":
        return fetch_gdelt_source(source)
    return []


def _http_get(url, **kwargs):
    headers = {
        "User-Agent": "SprouttCRM/1.0 customer-news-ingestion",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, application/json",
    }
    return requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)


def fetch_rss_source(source):
    response = _http_get(source.get("url"))
    response.raise_for_status()
    text = response.text.strip()
    if not text.startswith("<"):
        raise ValueError("Source did not return XML/HTML content")

    root = ElementTree.fromstring(response.content)
    items = []
    channel = root.find("channel")
    if channel is not None:
        source_name = _child_text(channel, "title") or source.get("name")
        for item in channel.findall("item"):
            items.append(
                _article_payload(
                    source,
                    title=_child_text(item, "title"),
                    url=_child_text(item, "link") or _guid_text(item),
                    published_at=parse_datetime(_child_text(item, "pubDate")),
                    source_name=source_name,
                    summary=_child_text(item, "description"),
                )
            )
        return [item for item in items if item.get("title") and item.get("url")]

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    source_name = _child_text(root, "atom:title", ns) or source.get("name")
    for entry in root.findall("atom:entry", ns):
        link = ""
        link_node = entry.find("atom:link", ns)
        if link_node is not None:
            link = link_node.attrib.get("href", "")
        items.append(
            _article_payload(
                source,
                title=_child_text(entry, "atom:title", ns),
                url=link,
                published_at=parse_datetime(_child_text(entry, "atom:published", ns) or _child_text(entry, "atom:updated", ns)),
                source_name=source_name,
                summary=_child_text(entry, "atom:summary", ns),
            )
        )
    return [item for item in items if item.get("title") and item.get("url")]


def discover_webpage_feeds(source):
    response = _http_get(source.get("url"))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    discovered = []
    for link in soup.find_all("link"):
        link_type = (link.get("type") or "").lower()
        href = link.get("href")
        if href and ("rss" in link_type or "atom" in link_type or "xml" in link_type):
            discovered.append((link.get("title") or href, urljoin(source.get("url"), href)))
    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        label = anchor.get_text(" ", strip=True) or href
        if href and re.search(r"(rss|feed|atom|xml)", href, re.I):
            discovered.append((label, urljoin(source.get("url"), href)))

    for label, url in discovered[:10]:
        _upsert_source(
            f"{source.get('name')} - {label}"[:240],
            "rss",
            url=url,
            sector_tag=source.get("sector_tag"),
            priority=source.get("priority") or 3,
            check_frequency_minutes=source.get("check_frequency_minutes") or 1440,
        )
    return []


def fetch_gdelt_source(source):
    response = _http_get(
        GDELT_ENDPOINT,
        params={
            "query": source.get("query"),
            "mode": "ArtList",
            "format": "json",
            "sort": "DateDesc",
            "maxrecords": 100,
        },
    )
    response.raise_for_status()
    data = response.json()
    articles = []
    for item in data.get("articles") or []:
        articles.append(
            _article_payload(
                source,
                title=item.get("title"),
                url=item.get("url"),
                published_at=parse_datetime(item.get("seendate")),
                source_name=item.get("sourceCollection") or item.get("domain") or source.get("name"),
                summary=item.get("snippet") or "",
                language=item.get("language"),
                country=item.get("sourceCountry"),
            )
        )
    return [item for item in articles if item.get("title") and item.get("url")]


def _child_text(node, path, ns=None):
    found = node.find(path, ns or {})
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _guid_text(item):
    guid = item.find("guid")
    return guid.text.strip() if guid is not None and guid.text else ""


def _article_payload(source, title, url, published_at=None, source_name=None, summary="", language=None, country=None):
    canonical = normalize_url(url)
    excerpt = BeautifulSoup(summary or "", "html.parser").get_text(" ", strip=True)
    normalized_title = normalize_title(title)
    return {
        "source_id": source.get("id"),
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "canonical_url": canonical,
        "source_domain": source_domain(canonical),
        "source_name": source_name or source.get("name"),
        "published_at": published_at,
        "summary_raw": summary or "",
        "body_excerpt": excerpt[:2000],
        "language": language,
        "country": country,
        "title_hash": digest(normalized_title),
        "content_hash": digest(f"{normalized_title} {excerpt[:500].lower()}"),
    }


def upsert_article(article):
    existing = db_execute(
        "SELECT id, title_hash, duplicate_of_article_id FROM news_articles WHERE canonical_url = ?",
        (article["canonical_url"],),
        fetch="one",
    )
    if existing:
        db_execute(
            """
            UPDATE news_articles
            SET fetched_at = CURRENT_TIMESTAMP,
                source_id = COALESCE(?, source_id),
                summary_raw = COALESCE(NULLIF(?, ''), summary_raw),
                body_excerpt = COALESCE(NULLIF(?, ''), body_excerpt),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (article.get("source_id"), article.get("summary_raw"), article.get("body_excerpt"), existing["id"]),
            commit=True,
        )
        return existing["id"], True

    duplicate_of = find_duplicate_article(article)
    row = db_execute(
        """
        INSERT INTO news_articles
            (source_id, title, url, canonical_url, source_domain, source_name, published_at,
             summary_raw, body_excerpt, language, country, content_hash, title_hash, duplicate_of_article_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            article.get("source_id"),
            article.get("title"),
            article.get("url"),
            article.get("canonical_url"),
            article.get("source_domain"),
            article.get("source_name"),
            article.get("published_at"),
            article.get("summary_raw"),
            article.get("body_excerpt"),
            article.get("language"),
            article.get("country"),
            article.get("content_hash"),
            article.get("title_hash"),
            duplicate_of,
        ),
        fetch="one",
        commit=True,
    )
    return (row["id"] if row else None), bool(duplicate_of)


def find_duplicate_article(article):
    published = article.get("published_at") or utc_now()
    row = db_execute(
        """
        SELECT id
        FROM news_articles
        WHERE title_hash = ?
          AND COALESCE(published_at, fetched_at) BETWEEN ? AND ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (article.get("title_hash"), published - timedelta(days=7), published + timedelta(days=7)),
        fetch="one",
    )
    if row:
        return row["id"]
    return None


def _mark_source_success(source_id):
    db_execute(
        """
        UPDATE news_sources
        SET last_checked_at = CURRENT_TIMESTAMP,
            last_success_at = CURRENT_TIMESTAMP,
            error_count = 0,
            last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (source_id,),
        commit=True,
    )


def _mark_source_error(source_id, error):
    db_execute(
        """
        UPDATE news_sources
        SET last_checked_at = CURRENT_TIMESTAMP,
            error_count = error_count + 1,
            last_error = ?,
            active = CASE WHEN error_count + 1 >= 5 THEN FALSE ELSE active END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (str(error)[:1000], source_id),
        commit=True,
    )


def get_customer_aliases():
    rows = db_execute(
        """
        SELECT ca.customer_id, c.name AS customer_name, ca.alias, ca.alias_type, ca.weight, c.website, c.watch
        FROM customer_aliases ca
        JOIN customers c ON c.id = ca.customer_id
        WHERE ca.active = TRUE
        UNION ALL
        SELECT c.id AS customer_id, c.name AS customer_name, c.name AS alias, 'legal_name' AS alias_type, 100 AS weight, c.website, c.watch
        FROM customers c
        WHERE c.name IS NOT NULL AND c.name <> ''
        """,
        fetch="all",
    ) or []
    aliases = []
    seen = set()
    for row in rows:
        key = (row["customer_id"], (row["alias"] or "").lower())
        if key in seen:
            continue
        seen.add(key)
        aliases.append(dict(row))
    return aliases


def match_article_to_customers(article_id, aliases=None):
    article = db_execute("SELECT * FROM news_articles WHERE id = ?", (article_id,), fetch="one")
    if not article or article.get("duplicate_of_article_id"):
        return 0
    text = f"{article.get('title') or ''}\n{article.get('body_excerpt') or ''}".strip()
    text_lower = text.lower()
    title_lower = (article.get("title") or "").lower()
    matched = 0
    for alias in aliases if aliases is not None else get_customer_aliases():
        alias_text = (alias.get("alias") or "").strip()
        if len(alias_text) < 3:
            continue
        if not alias_matches(alias_text, text, text_lower):
            continue
        short_alias = len(re.sub(r"\W+", "", alias_text)) <= 4
        if short_alias and not any(keyword in text_lower for keyword in AVIATION_KEYWORDS):
            continue
        score, reason = score_article(article, alias_text, alias.get("weight") or 80, title_lower, text_lower)
        db_execute(
            """
            INSERT INTO article_customer_mentions
                (article_id, customer_id, matched_alias, match_type, confidence, relevance_score, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (article_id, customer_id, matched_alias) DO UPDATE SET
                confidence = EXCLUDED.confidence,
                relevance_score = EXCLUDED.relevance_score,
                reason = EXCLUDED.reason
            """,
            (
                article_id,
                alias["customer_id"],
                alias_text,
                "exact",
                min(100, alias.get("weight") or 80),
                score,
                reason,
            ),
            commit=True,
        )
        matched += 1
    return matched


def alias_matches(alias, text, text_lower):
    if len(re.sub(r"\W+", "", alias)) <= 4 and alias.upper() == alias:
        return bool(re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", text))
    return bool(re.search(rf"\b{re.escape(alias.lower())}\b", text_lower))


def score_article(article, alias, alias_weight, title_lower, text_lower):
    score = 0
    reasons = []
    alias_lower = alias.lower()
    if alias_lower in title_lower:
        score += 50
        reasons.append("customer in title")
    elif alias_lower in text_lower:
        score += 35
        reasons.append("customer in excerpt")
    score += min(20, max(0, int(alias_weight) // 10))

    platforms = platform_mentions_in_text(text_lower)
    if platforms:
        score += 25
        reasons.append("platform: " + ", ".join(platforms[:3]))

    sectors = sector_mentions_in_text(text_lower)
    if sectors:
        score += 20
        reasons.append("sector: " + ", ".join(sectors[:3]))

    domain = article.get("source_domain") or ""
    if any(value in domain for value in ("helihub", "airbus", "leonardo", "easa", "npas")):
        score += 20
        reasons.append("high-value source")

    published = parse_datetime(article.get("published_at"))
    if published and published >= utc_now() - timedelta(days=7):
        score += 10
        reasons.append("recent")

    if any(keyword in text_lower for keyword in NEGATIVE_KEYWORDS):
        score -= 35
        reasons.append("low-value keyword")

    return max(0, min(100, score)), "; ".join(reasons)


def platform_mentions_in_text(text_lower):
    matches = []
    for platform in PLATFORMS:
        pattern = rf"\b{re.escape(platform.lower())}\b"
        if re.search(pattern, text_lower):
            matches.append(platform)
    return matches


def sector_mentions_in_text(text_lower):
    matches = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            matches.append(sector)
    return matches


def match_article_platforms(article_id):
    article = db_execute("SELECT * FROM news_articles WHERE id = ?", (article_id,), fetch="one")
    if not article:
        return 0
    text_lower = f"{article.get('title') or ''}\n{article.get('body_excerpt') or ''}".lower()
    matches = platform_mentions_in_text(text_lower)
    for platform in matches:
        db_execute(
            """
            INSERT INTO article_platform_mentions (article_id, platform, confidence)
            VALUES (?, ?, 85)
            ON CONFLICT (article_id, platform) DO NOTHING
            """,
            (article_id, platform),
            commit=True,
        )
    return len(matches)


def get_news_for_salesperson(salesperson_id, limit=20, days=45):
    return db_execute(
        """
        SELECT
            na.id AS article_id,
            na.title AS headline,
            COALESCE(NULLIF(na.body_excerpt, ''), NULLIF(na.summary_raw, '')) AS summary,
            na.source_name AS source,
            na.url,
            na.published_at::date AS published_date,
            acm.customer_id,
            c.name AS customer_name,
            COALESCE(c.watch, FALSE) AS is_watched,
            acm.relevance_score,
            CASE
                WHEN acm.relevance_score >= 80 THEN 'High'
                WHEN acm.relevance_score >= 60 THEN 'Medium'
                ELSE 'Low'
            END AS business_impact,
            acm.reason
        FROM article_customer_mentions acm
        JOIN news_articles na ON na.id = acm.article_id
        JOIN customers c ON c.id = acm.customer_id
        WHERE c.salesperson_id = ?
          AND na.duplicate_of_article_id IS NULL
          AND COALESCE(na.published_at, na.fetched_at) >= CURRENT_TIMESTAMP - (? * INTERVAL '1 day')
          AND acm.relevance_score >= 40
        ORDER BY COALESCE(c.watch, FALSE) DESC, acm.relevance_score DESC, COALESCE(na.published_at, na.fetched_at) DESC
        LIMIT ?
        """,
        (salesperson_id, days, limit),
        fetch="all",
    ) or []


def get_news_for_customer(customer_id, limit=10, days=90):
    return db_execute(
        """
        SELECT
            na.id AS article_id,
            na.title AS headline,
            COALESCE(NULLIF(na.body_excerpt, ''), NULLIF(na.summary_raw, '')) AS summary,
            na.source_name AS source,
            na.url,
            na.published_at::date AS published_date,
            acm.relevance_score,
            acm.reason
        FROM article_customer_mentions acm
        JOIN news_articles na ON na.id = acm.article_id
        WHERE acm.customer_id = ?
          AND na.duplicate_of_article_id IS NULL
          AND COALESCE(na.published_at, na.fetched_at) >= CURRENT_TIMESTAMP - (? * INTERVAL '1 day')
        ORDER BY acm.relevance_score DESC, COALESCE(na.published_at, na.fetched_at) DESC
        LIMIT ?
        """,
        (customer_id, days, limit),
        fetch="all",
    ) or []


def format_news_rows(rows):
    items = []
    for row in rows or []:
        item = dict(row)
        if item.get("published_date") is not None:
            item["published_date"] = str(item["published_date"])
        if item.get("summary"):
            item["summary"] = BeautifulSoup(str(item["summary"]), "html.parser").get_text(" ", strip=True)[:1000]
        items.append(item)
    return items


def record_feedback(article_id, customer_id, user_id, feedback_type, notes=""):
    db_execute(
        """
        INSERT INTO news_feedback (article_id, customer_id, user_id, feedback_type, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (article_id, customer_id, user_id, feedback_type, notes),
        commit=True,
    )


def export_result(result):
    return json.dumps(result, default=str)
