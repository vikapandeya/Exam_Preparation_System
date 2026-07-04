#!/usr/bin/env python3
"""
Fetch exam notification / job opening / result news from RSS feeds → write exam_updates_data.json
Runs via GitHub Actions daily. Uses stdlib only (no pip install needed).
Mirrors scripts/update_ca.py but filters for recruitment/exam-administration news
instead of general current affairs.
"""

import json, re, urllib.request, urllib.error, urllib.parse
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

# Google News search RSS — targeted queries give real, dated, sourced coverage
# of the last 30 days for each category (mainstream feeds above rarely carry
# routine recruitment notices in enough volume for a "last month" sync).
GOOGLE_NEWS_QUERIES = [
    ('notif', 'exam admit card OR notification released India when:30d'),
    ('notif', 'UPSC OR SSC OR RRB OR IBPS exam date notification when:30d'),
    ('job',   'sarkari naukri recruitment vacancy notification when:30d'),
    ('job',   'SSC OR UPSC OR RRB OR IBPS OR bank recruitment 2026 when:30d'),
    ('result','SSC OR UPSC OR RRB OR IBPS result declared when:30d'),
    ('result','sarkari result merit list declared when:30d'),
]

# Article must reference an exam/recruitment body or generic govt-job term ...
ORG_KW = [
    'upsc','ssc','ibps','rrb','railway recruitment','ntpc','bpsc','uppsc','rpsc',
    'mpsc','wbpsc','tnpsc','kpsc','opsc','jpsc','hpsc','mpsc','psc ','sbi po','sbi clerk',
    'rbi grade','nabard','sarkari','govt job','government job','teacher recruitment',
    'police recruitment','army recruitment','navy recruitment','air force recruitment',
    'airforce recruitment','indian army','ctet','ugc net','neet','jee ','gate exam',
    'clat','aiims','icar','isro recruitment','drdo recruitment','banking exam',
    'constable recruitment','patwari','anganwadi recruitment','nvs recruitment',
    'kvs recruitment','forest guard',
]
# ... AND match one of these category keyword sets (checked in priority order)
RESULT_KW = [
    'result declared','result released','results announced','result out','declares result',
    'merit list released','merit list out','cut off released','scorecard released',
    'final result','answer key released','result 2026','result 2025',
]
JOB_KW = [
    'recruitment','vacancy','vacancies','bharti','hiring for','various posts',
    'apply online','recruitment drive','notification for the post','group d','group c posts',
]
NOTIF_KW = [
    'exam date announced','admit card released','admit card 2026','admit card 2025',
    'exam postponed','notification released','application form','registration begins',
    'exam schedule','online registration','last date extended','eligibility criteria',
    'exam calendar','tier-i','tier-ii','prelims date','mains date','notification out',
]
BLACKLIST_KW = [
    'box office', 'bollywood', ' actor ', ' actress ', 'celebrity', 'film festival',
    'web series', 'ott release', 'dating', 'fashion week', 'reality show',
    'music video', 'trailer launch', 'movie review',
]

CAT_META = {
    'result': {'badge': 'Result',       'tc': 'tgs'},
    'job':    {'badge': 'Job Opening',  'tc': 'tge'},
    'notif':  {'badge': 'Notification', 'tc': 'tgi'},
}
MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']


def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', str(text or ''))
    text = re.sub(r'\s+', ' ', text)
    return unescape(text).strip()


def infer_cat(t: str):
    if any(kw in t for kw in RESULT_KW):
        return 'result'
    if any(kw in t for kw in JOB_KW):
        return 'job'
    if any(kw in t for kw in NOTIF_KW):
        return 'notif'
    return None


def is_relevant(t: str) -> bool:
    if any(bl in t for bl in BLACKLIST_KW):
        return False
    if not any(kw in t for kw in ORG_KW):
        return False
    return infer_cat(t) is not None


def fmt_date(pub: str) -> str:
    if not pub:
        return ''
    iso_m = re.match(r'(\d{4})-(\d{2})-(\d{2})', pub.strip())
    if iso_m:
        y, m, d = int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))
        if 1 <= m <= 12:
            return f"{MONTH_SHORT[m-1]} {d:02d}, {y}"
    m = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', pub, re.I)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
        month_idx = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                     'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}.get(mon, 1)
        return f"{MONTH_SHORT[month_idx-1]} {d:02d}, {y}"
    return pub[:16]


def parse_pub_dt(pub: str):
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def fetch_google_news(cat: str, query: str) -> list:
    articles = []
    url = 'https://news.google.com/rss/search?q=' + urllib.parse.quote(query) + '&hl=en-IN&gl=IN&ceid=IN:en'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ExamPrepBot/1.0; +https://github.com)',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        channel = root.find('channel'); channel = channel if channel is not None else root
        items = channel.findall('item') or []
        for item in items[:40]:
            raw_title = clean_html(item.findtext('title', ''))
            link      = (item.findtext('link', '') or '').strip()
            pub       = item.findtext('pubDate', '') or ''
            src_el    = item.find('source')
            source    = clean_html(src_el.text) if src_el is not None and src_el.text else 'Google News'
            # Google News titles are "Headline - Publisher"; strip the suffix since we have <source>
            title = re.sub(r'\s*-\s*' + re.escape(source) + r'\s*$', '', raw_title).strip() or raw_title
            if not title:
                continue
            t = title.lower()
            if any(bl in t for bl in BLACKLIST_KW):
                continue
            meta = CAT_META[cat]
            articles.append({
                'cat':    cat,
                'badge':  meta['badge'],
                'tc':     meta['tc'],
                'date':   fmt_date(pub),
                '_dt':    parse_pub_dt(pub),
                'title':  title,
                'sum':    title,
                'link':   link,
                'source': source,
                'exam':   f'Source: {source} · Auto-updated',
            })
    except urllib.error.URLError as e:
        print(f'  [Google News:{cat}] network error: {e.reason}')
    except ET.ParseError as e:
        print(f'  [Google News:{cat}] XML parse error: {e}')
    except Exception as e:
        print(f'  [Google News:{cat}] error: {e}')
    return articles


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
        channel = root.find('channel'); channel = channel if channel is not None else root
        items = channel.findall('item') or []
        for item in items[:20]:
            title = clean_html(item.findtext('title', ''))
            desc  = clean_html(item.findtext('description', ''))
            link  = (item.findtext('link', '') or '').strip()
            pub   = item.findtext('pubDate', '') or ''
            if not title:
                continue
            combined = (title + ' ' + desc).lower()
            if not is_relevant(combined):
                continue
            cat = infer_cat(combined)
            meta = CAT_META[cat]
            articles.append({
                'cat':    cat,
                'badge':  meta['badge'],
                'tc':     meta['tc'],
                'date':   fmt_date(pub),
                '_dt':    parse_pub_dt(pub),
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
    print(f'Fetching exam updates — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    all_articles = []
    for url, src in FEEDS:
        batch = fetch_feed(url, src)
        all_articles.extend(batch)
        print(f'  {src}: {len(batch)} relevant articles')

    for cat, query in GOOGLE_NEWS_QUERIES:
        batch = fetch_google_news(cat, query)
        all_articles.extend(batch)
        print(f'  Google News [{cat}] "{query}": {len(batch)} articles')

    # Keep only the last 31 days (Google News "when:30d" is approximate)
    cutoff = datetime.now(timezone.utc).timestamp() - 31 * 86400
    all_articles = [a for a in all_articles if a['_dt'].timestamp() >= cutoff]

    # Newest first
    all_articles.sort(key=lambda a: a['_dt'], reverse=True)

    seen, unique = set(), []
    for a in all_articles:
        key = re.sub(r'[^a-z0-9]', '', a['title'][:50].lower())
        if key not in seen:
            seen.add(key)
            a.pop('_dt', None)
            unique.append(a)

    for i, a in enumerate(unique, start=2000):
        a['id'] = i

    data = {
        'updated':      datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'updated_time': datetime.now(timezone.utc).strftime('%H:%M UTC'),
        'count':        len(unique),
        'articles':     unique[:250],
    }

    with open('exam_updates_data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'Written {len(unique)} articles → exam_updates_data.json')


if __name__ == '__main__':
    main()
