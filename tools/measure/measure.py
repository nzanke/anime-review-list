"""サイトの読み込みの速さを測る（改善の前後で同じ手順で使う）。

使い方：python3 tools/measure/measure.py ラベル
  リポジトリの写しを /tmp/arl-measure に作り、外部の字体（Google Fonts）の読み込みを外して、
  python3 -m http.server で配る。headless の Chrome（キャッシュ無効、毎回まっさらな状態）で
  各場面を5回ずつ開き、表示が終わるまでの時間の中央値と、読み込んだファイル・大きさを記録する。
  外部への接続（表紙画像など）は止めて測る（値をぶらさないため）。
  結果は tools/measure/result-ラベル.json に書く。

手元の配信は圧縮しないので、「圧縮後」の大きさは各ファイルを gzip（GitHub Pages と同じ強さ6）で
圧縮して計算した値。10Mbps での転送時間は、圧縮後の合計からの計算値。
"""
import gzip, json, os, re, shutil, statistics, subprocess, sys, time, urllib.parse
from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.environ.get('ARL_ROOT') or os.path.join(os.path.dirname(__file__), '..', '..'))  # 前の版を測るときは ARL_ROOT で指定
SITE = '/tmp/arl-measure'
PORT = 8799
BASE = f'http://localhost:{PORT}/'
RUNS = 5

SCENES = [
    # 名前, 画面幅, 開くURL, 操作, 終わりの条件（JavaScript）
    ('閲覧トップを開く', 390, '', None, "document.querySelector('#info').textContent.length>0"),
    ('閲覧で検索（スマホ）', 390, '', ('search', 'ガンダム'), "document.querySelectorAll('#results li').length>0"),
    ('閲覧で検索（PC）', 1100, '', ('search', 'ガンダム'), "document.querySelectorAll('#results li').length>0"),
    ('閲覧の個別ページ', 390, '#/movie/1981/f0311', None, "!!document.querySelector('.wh h2')"),
    ('閲覧で検索（スマホ・10Mbps模擬）', 390, '', ('search', 'ガンダム'), "document.querySelectorAll('#results li').length>0", True),
    ('閲覧の個別ページ（10Mbps模擬）', 390, '#/movie/1981/f0311', None, "!!document.querySelector('.wh h2')", True),
    ('編集で年を開く', 390, 'edit.html#movie', ('edit', '2024'), "document.querySelectorAll('details.yr[data-y=\"2024\"] details.work').length>0"),
]


def make_copy():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    shutil.copytree(ROOT, SITE, ignore=shutil.ignore_patterns('.git', 'tools', '.github'))
    for f in os.listdir(SITE):
        if f.endswith('.html'):
            p = os.path.join(SITE, f)
            s = open(p, encoding='utf-8').read()
            s = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>\n?', '', s)
            open(p, 'w', encoding='utf-8').write(s)


gz_cache = {}
def gz_size(path):
    if path not in gz_cache:
        p = os.path.join(SITE, urllib.parse.unquote(path).lstrip('/'))
        b = open(p, 'rb').read() if os.path.isfile(p) else b''
        gz_cache[path] = (len(b), len(gzip.compress(b, 6)) if not p.endswith('.woff2') else len(b))
    return gz_cache[path]


def run_scene(pw, scene):
    name, width, url, action, done = scene[:5]
    slow = len(scene) > 5 and scene[5]
    times, files, before = [], None, None
    browser = pw.chromium.launch()
    for i in range(RUNS):
        ctx = browser.new_context(viewport={'width': width, 'height': 844})
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send('Network.enable'); cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
        cdp.send('Network.setBlockedURLs', {'urls': ['*anilist.co*', '*googleapis.com*', '*gstatic.com*', '*microlink.io*']})  # 外部への接続は止める
        if slow:  # 10Mbps・往復40msの回線を模す
            cdp.send('Network.emulateNetworkConditions', {'offline': False, 'latency': 40,
                     'downloadThroughput': 10_000_000 / 8, 'uploadThroughput': 5_000_000 / 8})
        page.goto(BASE + url, wait_until='domcontentloaded')
        if action and action[0] == 'search':
            page.fill('#q', action[1])
        elif action and action[0] == 'edit':
            y = action[1]
            page.wait_for_selector('details.dec')
            page.click(f'summary:has-text("{y[:3]}0年代")')
            page.wait_for_selector(f'details.yr[data-y="{y}"]')
            page.click(f'details.yr[data-y="{y}"] > summary')
        try:
            t = page.wait_for_function(f'()=>({done})?performance.now():false', timeout=60000).json_value()
        except Exception as e:
            page.screenshot(path=f'/tmp/measure-fail-{i}.png')
            print('止まった場面：', name, i + 1, '回目', str(e)[:120], flush=True)
            raise
        times.append(round(t))
        if files is None:
            ents = page.evaluate("performance.getEntriesByType('resource').map(e=>[e.name,e.responseEnd])")
            top = {urllib.parse.urlparse(BASE + url).path or '/'}
            files = sorted({urllib.parse.urlparse(e).path for e, _ in ents if e.startswith(BASE)} | top)
            before = sorted({urllib.parse.urlparse(e).path for e, end in ents if e.startswith(BASE) and 0 < end <= t} | top)
        ctx.close()
    browser.close()
    sizes = [gz_size(f if not f.endswith('/') else f + 'index.html') for f in files]
    raw, gz = sum(s[0] for s in sizes), sum(s[1] for s in sizes)
    gz_before = sum(gz_size(f if not f.endswith('/') else f + 'index.html')[1] for f in before)
    return {'場面': name, '時間ms': times, '中央値ms': statistics.median(times),
            'ファイル': [f'{f}（{s[0]/1024:.1f}KB／圧縮後{s[1]/1024:.1f}KB）' for f, s in zip(files, sizes)],
            '件数': len(files), '圧縮前KB': round(raw / 1024, 1), '圧縮後KB': round(gz / 1024, 1),
            '10Mbps転送秒（計算値）': round(gz * 8 / 10_000_000, 2),
            '表示までに読み終えたファイル': before, '表示までの圧縮後KB': round(gz_before / 1024, 1),
            '表示までの10Mbps転送秒（計算値）': round(gz_before * 8 / 10_000_000, 2)}


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else 'now'
    make_copy()
    srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(PORT)], cwd=SITE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        with sync_playwright() as pw:
            res = []
            for s in SCENES:
                print('測定中：', s[0], flush=True)
                res.append(run_scene(pw, s))
    finally:
        srv.terminate()
    out = os.path.join(os.path.dirname(__file__), f'result-{label}.json')
    json.dump(res, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for r in res:
        print(f"{r['場面']}：中央値 {r['中央値ms']}ms {r['時間ms']}　全部 圧縮後{r['圧縮後KB']}KB（圧縮前{r['圧縮前KB']}KB）　表示まで 圧縮後{r['表示までの圧縮後KB']}KB・10Mbpsで{r['表示までの10Mbps転送秒（計算値）']}秒")


main()
