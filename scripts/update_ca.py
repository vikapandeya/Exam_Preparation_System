#!/usr/bin/env python3
"""
Fetch Indian exam current affairs from RSS feeds → write ca_data.json
Runs via GitHub Actions daily. Uses stdlib only (no pip install needed).
"""

import json, re, urllib.request, urllib.error
from datetime import datetime, timezone
from html import unescape
import xml.etree.ElementTree as ET

FEEDS = [
    ('https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3',        'PIB India'),
    ('https://www.thehindu.com/news/national/feeder/default.rss',       'The Hindu'),
    ('https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms',    'Times of India'),
    ('https://feeds.bbci.co.uk/news/world/asia/india/rss.xml',          'BBC India'),
    ('https://www.indiatoday.in/rss/home',                              'India Today'),
]

# Only keep articles matching at least one of these keywords
EXAM_KW = [
    'appoint','award','launch','scheme','minister','president','governor',
    'budget','railway','defence','defense','mission','satellite','summit',
    'agreement','rank','index','report','committee','legislation','ordinance',
    'medal','champion','tiger','national park','world record','isro','drdo',
    'rbi','sebi','export','inflation','treaty','accord','g20','policy',
    'brics','united nations','climate','carbon','solar','olympic','cricket',
    'pm modi','india wins','india signs','india launches','india achieves',
    'upsc','ssc','bpsc','daroga','railway recruitment','government scheme',
    'parliament','cabinet','supreme court','high court','election commission',
]
# Short keywords matched with word-boundary regex to avoid false positives
EXAM_KW_WORD = ['gdp', 'fta', 'bill', 'act']
# Blacklist: skip articles containing these entertainment/noise keywords
BLACKLIST_KW = [
    'box office', 'bollywood', ' actor ', ' actress ', 'celebrity', 'film festival',
    'web series', 'ott release', 'dating', 'fashion week', 'reality show',
    'music video', 'trailer launch', 'movie review',
]

# Category inference (ordered — first match wins; last entry is default)
CAT_RULES = [
    ('eco',  ['rbi','sebi','gdp','inflation','budget','rupee','fiscal','tax','gst','trade','export','import','economy','repo rate','monetary policy','bank credit']),
    ('sci',  ['isro','drdo','nasa','satellite','space','missile propulsion','tech','digital','artificial intel','robot','moon mission','mars','iit research','ntpc']),
    ('def',  ['army','navy','airforce','air force','defence','defense','military','exercise ','drill','soldier','border','weapon','fighter jet','ins ','warship']),
    ('spo',  ['cricket','hockey','football','tennis','olympic','commonwealth games','medal','gold medal','silver medal','bronze medal','champion','tournament','world cup','ipl','kabaddi']),
    ('env',  ['forest','tiger','elephant','rhino','climate change','carbon','renewable','solar power','wind energy','pollution','biodiversity','wildlife','national park','mangrove']),
    ('int',  ['g20','brics','saarc','asean','nato','imf','world bank','summit','treaty','diplomat','foreign minister','united nations','un security','quad','bilateral']),
    ('awa',  ['award','padma','bharat ratna','oscar','grammy','nobel prize','felicitat','honour','felicitation']),
    ('nat',  []),  # catch-all
]

TC_MAP    = {'nat':'tgn','eco':'tge','sci':'tgs','def':'tgd','spo':'tgsp','env':'tgev','int':'tgi','awa':'tgi'}
BADGE_MAP = {'nat':'National','eco':'Economy','sci':'Science & Tech','def':'Defence',
             'spo':'Sports','env':'Environment','int':'International','awa':'Awards'}
MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']


def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', str(text or ''))
    text = re.sub(r'\s+', ' ', text)
    return unescape(text).strip()


def infer_cat(text: str) -> str:
    t = text.lower()
    for cat, kws in CAT_RULES:
        if not kws:
            return cat
        if any(kw in t for kw in kws):
            return cat
    return 'nat'


def fmt_date(pub: str) -> str:
    if not pub:
        return ''
    # Handle ISO format: 2026-07-03T10:12 or 2026-07-03 10:12
    iso_m = re.match(r'(\d{4})-(\d{2})-(\d{2})', pub.strip())
    if iso_m:
        y, m, d = int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))
        if 1 <= m <= 12:
            return f"{MONTH_SHORT[m-1]} {d:02d}, {y}"
    # Handle RSS pubDate: Mon, 03 Jul 2026 10:12:00 GMT
    m = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', pub, re.I)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
        month_idx = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                     'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}.get(mon, 1)
        return f"{MONTH_SHORT[month_idx-1]} {d:02d}, {y}"
    return pub[:16]


def is_exam_relevant(combined: str) -> bool:
    t = combined.lower()
    # Blacklist check first
    if any(bl in t for bl in BLACKLIST_KW):
        return False
    # Substring keywords
    if any(kw in t for kw in EXAM_KW):
        return True
    # Word-boundary keywords
    if any(re.search(r'\b' + re.escape(kw) + r'\b', t) for kw in EXAM_KW_WORD):
        return True
    return False


def fetch_feed(url: str, source: str) -> list:
    articles = []
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ExamPrepBot/1.0; +https://github.com)',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        channel = root.find('channel') or root
        items = channel.findall('item') or []
        for item in items[:12]:
            title = clean_html(item.findtext('title', ''))
            desc  = clean_html(item.findtext('description', ''))
            link  = (item.findtext('link', '') or '').strip()
            pub   = item.findtext('pubDate', '') or ''
            if not title:
                continue
            combined = (title + ' ' + desc).lower()
            if not is_exam_relevant(combined):
                continue
            cat = infer_cat(combined)
            articles.append({
                'cat':    cat,
                'badge':  BADGE_MAP[cat],
                'tc':     TC_MAP.get(cat, 'tgn'),
                'date':   fmt_date(pub),
                'title':  title,
                'sum':    (desc[:280] + '...') if len(desc) > 280 else desc or title,
                'link':   link,
                'source': source,
                'exam':   f'Source: {source} · Auto-updated',
            })
    except urllib.error.URLError as e:
        print(f'  [{source}] network error: {e.reason}')
    except ET.ParseError as e:
        print(f'  [{source}] XML parse error: {e}')
    except Exception as e:
        print(f'  [{source}] error: {e}')
    return articles


def main():
    print(f'Fetching current affairs — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    all_articles = []
    for url, src in FEEDS:
        batch = fetch_feed(url, src)
        all_articles.extend(batch)
        print(f'  {src}: {len(batch)} exam-relevant articles')

    # Deduplicate by normalised title prefix
    seen, unique = set(), []
    for a in all_articles:
        key = re.sub(r'[^a-z0-9]', '', a['title'][:50].lower())
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Assign IDs starting from 1000
    for i, a in enumerate(unique, start=1000):
        a['id'] = i

    data = {
        'updated':      datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'updated_time': datetime.now(timezone.utc).strftime('%H:%M UTC'),
        'count':        len(unique),
        'articles':     unique[:25],
    }

    with open('ca_data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'Written {len(unique)} articles → ca_data.json')


if __name__ == '__main__':
    main()
