"""anime-database の SQLite（作品台帳）から、ページが読む年ごとの作品一覧を書き出す。

使い方：python3 tools/build_catalog.py ../anime-database/data/db/anime_credits.sqlite
出力：catalog/index.json と catalog/{movie,tv}/<年>.json
  各年のファイルは [作品ID, 題名, 月日(MM-DD または空), 制作会社, 監督] の配列。
  映画は6番目に上映時間（分。不明なら 0）を足す。上映時間は同じリポジトリの data/films/映画台帳.csv から作品IDで引く。
  テレビのクールはページ側で放送開始月から決める（1〜3月＝冬、4〜6＝春、7〜9＝夏、10〜12＝秋）。
レビュー・受賞歴などページ上の編集は data/ にあり、ここでは触らない。
"""
import json, os, re, shutil, sqlite3, sys

db = sys.argv[1]
# 依頼者が「完全に不要」とした作品（作品ID→題名）。書き出しから外す
here = os.path.dirname(os.path.abspath(__file__))
exclude_path = os.path.join(here, '..', 'data', 'exclude.json')
exclude = json.load(open(exclude_path, encoding='utf-8')) if os.path.exists(exclude_path) else {}
import csv
film_csv = os.path.join(os.path.dirname(os.path.abspath(db)), '..', 'films', '映画台帳.csv')
minutes = {}
if os.path.exists(film_csv):
    for x in csv.DictReader(open(film_csv, encoding='utf-8-sig')):
        m = re.search(r'(\d+)\s*分', x.get('上映時間', ''))
        if m: minutes[x['作品ID']] = int(m.group(1))
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'catalog')
c = sqlite3.connect(db)
rows = c.execute("""select w.work_id, w.era, w.title, w.start_date, w.studio,
  coalesce(m.chief_director, m.director) from works w join work_main m using(work_id)""").fetchall()

out = {'movie': {}, 'tv': {}}
for wid, era, title, date, studio, director in rows:
    m = re.match(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?', date or '')
    if not m:
        continue
    if wid in exclude:
        continue
    kind = 'movie' if era == '映画' else 'tv'
    md = (m.group(2) or '') + ('-' + m.group(3) if m.group(3) else '') if m.group(2) else ''
    row = [wid, title, md, studio or '', director or '']
    if kind == 'movie': row.append(minutes.get(wid, 0))
    out[kind].setdefault(m.group(1), []).append(row)

if os.path.isdir(root):
    shutil.rmtree(root)
index = {}
for kind, years in out.items():
    os.makedirs(os.path.join(root, kind))
    index[kind] = {}
    for y, ws in years.items():
        ws.sort(key=lambda w: (w[2] or '99', w[1]))
        with open(os.path.join(root, kind, y + '.json'), 'w', encoding='utf-8') as f:
            json.dump(ws, f, ensure_ascii=False, separators=(',', ':'))
        index[kind][y] = len(ws)
with open(os.path.join(root, 'index.json'), 'w', encoding='utf-8') as f:
    json.dump(index, f, ensure_ascii=False, separators=(',', ':'))
print('上映時間あり', len(minutes)); print({k: (len(v), sum(v.values())) for k, v in index.items()})
