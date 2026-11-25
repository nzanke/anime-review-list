"""台帳の作品を TMDB と題名・公開年で結び付け、ポスター画像の場所を一覧にする（GitHub Actions で動かす）。

使い方：TMDB_API_KEY=… python3 tools/tmdb_match.py [--only-new]
出力：
  catalog/posters.json      … 確かに結び付いた作品 {作品ID: [TMDBの種類と番号, ポスターの場所]}
  data/tmdb-unsure.json     … 迷う作品（候補つき）。確認ページで依頼者が選ぶ
  data/tmdb-decisions.json  … 依頼者の判断 {作品ID: "movie/123" または "none"}。照合し直しても最優先で使い、消さない
"""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ['TMDB_API_KEY']
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
P = lambda *a: os.path.join(ROOT, *a)


def load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except FileNotFoundError:
        return default


def api(path, **q):
    q['api_key'] = KEY
    url = 'https://api.themoviedb.org/3' + path + '?' + urllib.parse.urlencode(q)
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + i * 2); continue
            if e.code == 404:
                return {}
            raise
        except Exception:
            time.sleep(1 + i)
    return {}


def norm(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    s = re.sub(r'[ァ-ヶ]', lambda m: chr(ord(m.group()) - 0x60), s)
    return re.sub(r'[\s・･.,、。!?「」『』【】\[\]()（）〈〉〜~\-–—―:：;；\'"’”/／♥☆★]', '', s)


def core(title):
    """検索に使う題名：劇場版・映画などの頭書きと、（第2期）などの補足を外す"""
    t = re.sub(r'^(劇場版|映画|劇場アニメ|アニメ映画)\s*', '', title)
    t = re.sub(r'[（(][^）)]*(期|シーズン|season|第\d+)[^）)]*[）)]\s*$', '', t, flags=re.I)
    return t.strip()


def candidates(kind, title, year):
    path = '/search/movie' if kind == 'movie' else '/search/tv'
    out = []
    for q in dict.fromkeys([title, core(title)]):
        r = api(path, query=q, language='ja-JP', include_adult='false')
        for x in r.get('results', [])[:8]:
            d = x.get('release_date') or x.get('first_air_date') or ''
            out.append({'tid': f"{kind}/{x['id']}", 'title': x.get('title') or x.get('name') or '',
                        'orig': x.get('original_title') or x.get('original_name') or '',
                        'date': d, 'lang': x.get('original_language'), 'poster': x.get('poster_path')})
        if out:
            break
    seen, uniq = set(), []
    for c in out:
        if c['tid'] not in seen:
            seen.add(c['tid']); uniq.append(c)
    return uniq


def judge(kind, title, year, cands):
    """確か（sure）／迷う（unsure）／なし（none）を決める"""
    nt, nc = norm(title), norm(core(title))
    scored = []
    for c in cands:
        names = {norm(c['title']), norm(c['orig'])}
        cy = c['date'][:4]
        name_ok = nt in names or nc in names
        year_ok = cy == year
        near = cy.isdigit() and abs(int(cy) - int(year)) <= 1
        ja = c['lang'] == 'ja'
        s = (3 if name_ok else 0) + (2 if year_ok else 1 if near else 0) + (1 if ja else 0)
        scored.append((s, name_ok, year_ok, near, ja, c))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return 'none', None, []
    top = scored[0]
    if top[1] and top[2] and (len(scored) == 1 or scored[1][0] < top[0]):
        return 'sure', top[5], []
    good = [x[5] for x in scored if x[0] >= 3][:4]
    if good:
        return 'unsure', None, good
    return 'none', None, []


def main():
    rows = json.load(open(P('catalog', 'search-titles.json'), encoding='utf-8'))
    decisions = load(P('data', 'tmdb-decisions.json'), {})
    posters = load(P('catalog', 'posters.json'), {}) if '--only-new' in sys.argv else {}
    todo = [r for r in rows if r[0] not in decisions and r[0] not in posters]
    print('照合する作品', len(todo), '件（依頼者の判断済み', len(decisions), '件）', flush=True)

    def work(r):
        wid, title, date = r[0], r[1], r[2]
        kind = 'movie' if wid.startswith('f') else 'tv'
        cands = candidates(kind, title, date[:4])
        return wid, title, date, judge(kind, title, date[:4], cands)

    unsure, done = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for wid, title, date, (res, best, cands) in ex.map(work, todo):
            done += 1
            if res == 'sure' and best['poster']:
                posters[wid] = [best['tid'], best['poster']]
            elif res == 'unsure':
                unsure.append({'id': wid, 'title': title, 'date': date,
                               'cands': [{k: c[k] for k in ('tid', 'title', 'orig', 'date', 'poster')} for c in cands]})
            if done % 500 == 0:
                print(done, '件済み', flush=True)

    # 依頼者の判断を反映（選んだ候補のポスターを取り直す）
    for wid, tid in decisions.items():
        if tid == 'none':
            posters.pop(wid, None); continue
        kind, num = tid.split('/')
        d = api(f'/{kind}/{num}', language='ja-JP')
        if d.get('poster_path'):
            posters[wid] = [tid, d['poster_path']]

    json.dump(dict(sorted(posters.items())), open(P('catalog', 'posters.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    json.dump(unsure, open(P('data', 'tmdb-unsure.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('ポスターあり', len(posters), '迷う', len(unsure), flush=True)


main()
