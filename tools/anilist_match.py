"""台帳の作品を AniList と原題・公開年・形式で結び付け、表紙画像の名前を一覧にする（GitHub Actions で動かす）。
AniList の API はキー不要・非営利は無料。いまは1分30回の制限があるので、1回の問い合わせで10作品ずつまとめて調べる。

使い方：python3 tools/anilist_match.py [--only-new]
出力：
  catalog/thumbs.json        … 確かに結び付いた作品 {作品ID: 表紙画像の名前}（閲覧ページが使う）
  data/anilist-match.json    … 結び付けの記録 {作品ID: AniListの作品番号}
  data/anilist-unsure.json   … 迷う作品（候補つき）。確認ページで依頼者が選ぶ
  data/anilist-decisions.json… 依頼者の判断 {作品ID: AniListの作品番号 または "none"}。照合し直しても最優先で使い、消さない
"""
import json, os, re, sys, time, unicodedata, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
P = lambda *a: os.path.join(ROOT, *a)
API = 'https://graphql.anilist.co'
BATCH = 10
FIELDS = 'id title{native romaji} format startDate{year month day} coverImage{large}'


def load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except FileNotFoundError:
        return default


def save(path, data, compact=False):
    with open(path, 'w', encoding='utf-8') as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)


last = [0.0]
def gql(query, variables):
    for i in range(8):
        wait = 2.1 - (time.time() - last[0])   # 1分30回に収める
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time()
        req = urllib.request.Request(API, data=json.dumps({'query': query, 'variables': variables}).encode(),
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get('data') or {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get('Retry-After') or 60) + 1); continue
            if e.code >= 500:
                time.sleep(10 * (i + 1)); continue
            body = e.read().decode('utf-8', 'replace')[:300]
            print('問い合わせの誤り', e.code, body, flush=True)
            return {}
        except Exception as e:
            time.sleep(5 * (i + 1))
    return {}


def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    s = re.sub(r'[ァ-ヶ]', lambda m: chr(ord(m.group()) - 0x60), s)
    return re.sub(r'[\s・･.,、。!?「」『』【】\[\]()（）〈〉〜~\-–—―:：;；\'"’”/／♥☆★♪]', '', s)


def core(title):
    """検索に使う題名：劇場版・映画などの頭書きと、（第2期）などの補足を外す"""
    t = re.sub(r'^(劇場版|映画|劇場アニメ|アニメ映画)\s*', '', title)
    t = re.sub(r'[（(][^）)]*(期|シーズン|season|第\d+|シリーズ)[^）)]*[）)]\s*$', '', t, flags=re.I)
    return t.strip() or title


MOVIE_FMT = {'MOVIE', 'SPECIAL', 'OVA'}
TV_FMT = {'TV', 'TV_SHORT', 'ONA', 'SPECIAL'}


def judge(kind, title, year, cands):
    nt, nc = norm(title), norm(core(title))
    scored = []
    for c in cands:
        names = {norm(c['title']['native']), norm(c['title']['romaji'])}
        cy = (c.get('startDate') or {}).get('year')
        name_ok = nt in names or nc in names
        part = not name_ok and any(n and (nc in n or n in nc) and min(len(n), len(nc)) >= 3 for n in names)
        year_ok = cy == int(year)
        near = cy is not None and abs(cy - int(year)) <= 1
        fmt_ok = c.get('format') in (MOVIE_FMT if kind == 'movie' else TV_FMT)
        s = (4 if name_ok else 2 if part else 0) + (3 if year_ok else 1 if near else 0) + (1 if fmt_ok else 0)
        scored.append((s, name_ok, year_ok, fmt_ok, c))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return 'none', None, []
    top = scored[0]
    if top[1] and top[2] and top[3] and (len(scored) == 1 or scored[1][0] < top[0]):
        return 'sure', top[4], []
    good = [x[4] for x in scored if x[0] >= 5][:4]
    return ('unsure', None, good) if good else ('none', None, [])


def cover_name(c):
    url = ((c or {}).get('coverImage') or {}).get('large') or ''
    if not url or 'default' in url:
        return ''
    return url.rsplit('/', 1)[-1]


def main():
    rows = json.load(open(P('catalog', 'search-titles.json'), encoding='utf-8'))
    decisions = load(P('data', 'anilist-decisions.json'), {})
    only_new = '--only-new' in sys.argv
    apply_only = '--apply' in sys.argv   # 依頼者の判断を当てはめるだけ（照合はしない）
    keep = only_new or apply_only
    thumbs = load(P('catalog', 'thumbs.json'), {}) if keep else {}
    match = load(P('data', 'anilist-match.json'), {}) if keep else {}
    todo = [] if apply_only else [r for r in rows if r[0] not in decisions and r[0] not in match]
    print('照合する作品', len(todo), '件（依頼者の判断済み', len(decisions), '件）', flush=True)

    def make_query(n):
        vars_def = ', '.join(f'$s{i}: String' for i in range(n))
        body = ' '.join(f'a{i}: Page(perPage: 6) {{ media(search: $s{i}, type: ANIME) {{ {FIELDS} }} }}' for i in range(n))
        return f'query ({vars_def}) {{ {body} }}'
    unsure = [u for u in load(P('data', 'anilist-unsure.json'), []) if u['id'] not in decisions] if apply_only else []
    for b in range(0, len(todo), BATCH):
        chunk = todo[b:b + BATCH]
        data = gql(make_query(len(chunk)), {f's{i}': core(r[1]) for i, r in enumerate(chunk)})
        for i, r in enumerate(chunk):
            wid, title, date = r[0], r[1], r[2]
            kind = 'movie' if wid.startswith('f') else 'tv'
            cands = ((data.get(f'a{i}') or {}).get('media')) or []
            res, best, good = judge(kind, title, date[:4], cands)
            if res == 'sure':
                match[wid] = best['id']
                n = cover_name(best)
                if n:
                    thumbs[wid] = n
            elif res == 'unsure':
                unsure.append({'id': wid, 'title': title, 'date': date,
                               'cands': [{'aid': c['id'], 'title': c['title']['native'] or c['title']['romaji'],
                                          'format': c.get('format'), 'year': (c.get('startDate') or {}).get('year'),
                                          'cover': cover_name(c)} for c in good]})
        if (b // BATCH) % 50 == 0:
            print(b + len(chunk), '/', len(todo), '件済み　結び付いた', len(match), '迷う', len(unsure), flush=True)
            save(P('catalog', 'thumbs.json'), dict(sorted(thumbs.items())), compact=True)

    # 依頼者の判断を反映（選んだ作品の表紙を取り直す）
    picked = [(w, a) for w, a in decisions.items() if a != 'none']
    for w, a in decisions.items():
        if a == 'none':
            match.pop(w, None); thumbs.pop(w, None)
    for b in range(0, len(picked), BATCH):
        chunk = picked[b:b + BATCH]
        q = 'query { ' + ' '.join(f'a{i}: Media(id: {int(a)}) {{ {FIELDS} }}' for i, (w, a) in enumerate(chunk)) + ' }'
        data = gql(q, {})
        for i, (w, a) in enumerate(chunk):
            match[w] = int(a)
            n = cover_name(data.get(f'a{i}'))
            if n:
                thumbs[w] = n

    save(P('catalog', 'thumbs.json'), dict(sorted(thumbs.items())), compact=True)
    save(P('data', 'anilist-match.json'), dict(sorted(match.items())), compact=True)
    save(P('data', 'anilist-unsure.json'), unsure)
    print('結び付いた', len(match), '表紙あり', len(thumbs), '迷う', len(unsure), flush=True)


main()
