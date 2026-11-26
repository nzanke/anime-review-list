"""表紙の一覧（catalog/thumbs.json）を年ごとに分け、ページの読み込みの目印（?v=）を付け直す。

使い方：python3 tools/split_thumbs.py
  catalog/thumbs/{movie,tv}/<年>.json … その年の作品の {作品ID: 表紙画像の名前}（個別ページが使う）
  index.html と edit.html の「const CATV='…'」を今の日時に書き換える（台帳・表紙を作り直したら毎回）
build_catalog.py と anilist_match.py の最後に呼ばれる。
"""
import json, os, re, shutil, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
P = lambda *a: os.path.join(ROOT, *a)


def split():
    thumbs = json.load(open(P('catalog', 'thumbs.json'), encoding='utf-8')) if os.path.exists(P('catalog', 'thumbs.json')) else {}
    rows = json.load(open(P('catalog', 'search-titles.json'), encoding='utf-8'))
    out = {}
    for r in rows:
        if r[0] in thumbs:
            kind = 'movie' if r[0].startswith('f') else 'tv'
            out.setdefault((kind, r[2][:4]), {})[r[0]] = thumbs[r[0]]
    if os.path.isdir(P('catalog', 'thumbs')):
        shutil.rmtree(P('catalog', 'thumbs'))
    for (kind, y), d in out.items():
        os.makedirs(P('catalog', 'thumbs', kind), exist_ok=True)
        json.dump(dict(sorted(d.items())), open(P('catalog', 'thumbs', kind, y + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
    return len(out)


def stamp():
    v = time.strftime('%Y%m%d-%H%M', time.gmtime(time.time() + 9 * 3600))
    for f in ('index.html', 'edit.html'):
        s = open(P(f), encoding='utf-8').read()
        s2 = re.sub(r"const CATV='[^']*';", f"const CATV='{v}';", s)
        if s2 != s:
            open(P(f), 'w', encoding='utf-8').write(s2)
    return v


if __name__ == '__main__':
    print('年ごとの表紙の一覧', split(), '件　目印', stamp())
