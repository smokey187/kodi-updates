# service.subs.multilang - when Elementum starts playing, download the 3 best-ranked subtitles per language
# via a4kSubtitles (its ranking puts hash/release-synced results first) and load them all into the player.
import os, re, shutil, sys, threading, time
import xbmc, xbmcaddon, xbmcvfs

LANGS = [('English', 'en'), ('Polish', 'pl'), ('Hebrew', 'he')]   # first language = the one left active
PER_LANG = 3
OUT = xbmcvfs.translatePath('special://temp/multilang_subs/')
A4K = xbmcaddon.Addon('service.subtitles.a4ksubtitles').getAddonInfo('path')
if A4K not in sys.path:
    sys.path.insert(0, A4K)



FALLBACK_ENC = {'he': ('cp1255', 'iso-8859-8'), 'pl': ('cp1250', 'iso-8859-2'), 'en': ('cp1252',)}


def to_utf8(src, dst, code):
    """Copy a subtitle as UTF-8 (with BOM) so Kodi never shows broken Hebrew/Polish letters."""
    try:
        with open(src, 'rb') as f:
            raw = f.read()
        text = None
        for enc in ('utf-8-sig',) + FALLBACK_ENC.get(code, ()) + ('cp1252',):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode('utf-8', 'replace')
        with open(dst, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(text)
    except Exception:
        shutil.copyfile(src, dst)



# ---------- Wizdom (Israeli Hebrew subtitle site, wizdom.xyz) ----------
WIZDOM = 'https://wizdom.xyz/api'


def _http(url, timeout=12):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'Kodi/21 service.subs.multilang'})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _tokens(name):
    return [t for t in re.split(r'[^a-z0-9]+', name.lower()) if t]


def _show_imdb(title):
    """IMDb id of a TV show by its title (same public suggestion API a4kSubtitles uses)."""
    import json, urllib.parse
    q = re.sub(r'[^a-z0-9 ]', '', title.lower()).strip().replace(' ', '_')
    if not q:
        return ''
    data = json.loads(_http('https://v2.sg.media-imdb.com/suggestion/%s/%s.json' % (q[0], urllib.parse.quote(q))).decode('utf-8'))
    for r in data.get('d', []):
        if r.get('id', '').startswith('tt') and r.get('qid') in ('tvSeries', 'tvMiniSeries'):
            return r['id']
    return ''


def wizdom_search(fname):
    """Hebrew candidates from Wizdom, best release match first."""
    import json
    season = xbmc.getInfoLabel('VideoPlayer.Season') or '0'
    episode = xbmc.getInfoLabel('VideoPlayer.Episode') or '0'
    if season != '0' and episode != '0':
        imdb = _show_imdb(xbmc.getInfoLabel('VideoPlayer.TVShowTitle'))
    else:
        imdb, season, episode = xbmc.getInfoLabel('VideoPlayer.IMDBNumber'), '0', '0'
    if not imdb.startswith('tt'):
        return []
    data = json.loads(_http('%s/search?action=by_id&imdb=%s&season=%s&episode=%s&version=0' % (WIZDOM, imdb, season, episode)).decode('utf-8'))
    if not isinstance(data, list):
        return []
    ft = set(_tokens(fname))
    out = []
    for r in data:
        vt = _tokens(r.get('versioname', ''))
        if not vt:
            continue
        score = len([t for t in vt if t in ft]) / float(len(vt))
        same_group = vt[-1] in ft
        out.append({'service_name': 'wizdom', 'name': r.get('versioname', ''), 'lang': 'Hebrew',
                    'sync': 'true' if (score >= 0.75 or (same_group and score >= 0.5)) else 'false',
                    '_score': score, '_wizdom_id': r.get('id')})
    out.sort(key=lambda r: -r['_score'])
    log('wizdom: %d results for %s S%sE%s' % (len(out), imdb, season, episode))
    return out


def wizdom_download(sub_id):
    import io, zipfile
    raw = _http('%s/files/sub/%s' % (WIZDOM, sub_id), timeout=20)
    tmp = os.path.join(OUT, '_wizdom_%s' % sub_id)
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(('.srt', '.ass', '.ssa', '.sub', '.vtt'))]
        names.sort(key=lambda n: (not n.lower().endswith('.srt'), n))
        if not names:
            return None
        dst = os.path.join(tmp, os.path.basename(names[0]))
        with open(dst, 'wb') as f:
            f.write(z.read(names[0]))
        return dst



def key_of(r):
    return '%s|%s' % (r.get('service_name', ''), (r.get('name') or '').lower())


def remember_key(r):
    """Keep track of every subtitle already downloaded for this video (used by 'download more')."""
    import json
    p = os.path.join(OUT, 'state.json')
    try:
        with open(p) as f:
            st = json.load(f)
    except Exception:
        st = {'keys': []}
    if key_of(r) not in st['keys']:
        st['keys'].append(key_of(r))
    with open(p, 'w') as f:
        json.dump(st, f)

def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[MultiLangSubs] %s' % msg, level)


def is_elementum(path):
    return 'elementum' in path.lower() or ':65220/' in path


def safe(name):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:80].strip('._') or 'sub'


def run(token, monitor, player):
    try:
        # wait for Elementum to publish the video meta (a4k needs IMDb/title from VideoPlayer.*)
        for _ in range(40):
            if monitor.abortRequested() or token != state['token']:
                return
            if xbmc.getInfoLabel('VideoPlayer.IMDBNumber') or xbmc.getInfoLabel('VideoPlayer.Title'):
                break
            monitor.waitForAbort(0.5)
        from a4kSubtitles import api as a4kapi
        api = a4kapi.A4kSubtitlesApi()
        shutil.rmtree(OUT, ignore_errors=True)
        os.makedirs(OUT, exist_ok=True)
        loaded = []
        # one search for all languages at once (much faster than one search per language)
        t0 = time.time()
        try:
            all_results = api.search({'languages': ','.join(l for l, _ in LANGS), 'preferredlanguage': LANGS[0][0]}) or []
        except Exception as e:
            log('search failed: %s' % e, xbmc.LOGWARNING)
            all_results = []
        log('search: %d results in %.1fs' % (len(all_results), time.time() - t0))
        for lang, code in LANGS:
            if token != state['token']:
                return
            results = [r for r in all_results if r.get('lang') == lang]
            if code == 'he':  # add Wizdom (Israeli Hebrew site) and rank: synced first, Wizdom before others
                try:
                    wz = wizdom_search(player.getPlayingFile().rsplit('/', 1)[-1])
                except Exception as e:
                    log('wizdom search failed: %s' % e, xbmc.LOGWARNING)
                    wz = []
                results = [r for r in results if r.get('lang') == lang]
                results = sorted(wz + results, key=lambda r: (r.get('sync') != 'true', r.get('service_name') != 'wizdom'))
            picks, seen = [], set()
            for r in results:
                if r.get('lang') != lang:
                    continue
                key = (r.get('service_name'), (r.get('name') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                picks.append(r)
                if len(picks) == PER_LANG:
                    break
            n = 0
            for r in picks:
                try:
                    src = wizdom_download(r['_wizdom_id']) if r.get('_wizdom_id') else api.download(r)
                except Exception as e:
                    log('download failed (%s/%s): %s' % (lang, r.get('service_name'), e), xbmc.LOGWARNING)
                    continue
                if not src or not os.path.isfile(src):
                    continue
                n += 1
                ext = os.path.splitext(src)[1] or '.srt'
                sync = r.get('sync') == 'true'
                dst = os.path.join(OUT, '%d %s%s.%s%s' % (n, 'sync ' if sync else '', safe(r.get('service_name', '')).replace('_', '-') or 'sub', code, ext))
                to_utf8(src, dst, code)
                loaded.append((lang, n, r.get('sync') == 'true', dst))
                remember_key(r)
            log('%s: %d/%d downloaded (results: %d)' % (lang, n, len(picks), len(results)))
        if token != state['token'] or not player.isPlayingVideo() or not is_elementum(player.getPlayingFile()):
            return
        # add in reverse so the very best subtitle of the first language ends up as the active one
        for lang, n, sync, path in reversed(loaded):
            player.setSubtitles(path)
            monitor.waitForAbort(0.2)
        log('loaded %d subtitles: %s' % (len(loaded), ', '.join('%s#%d%s' % (l, n, '(sync)' if s else '') for l, n, s, _ in loaded)))
    except Exception as e:
        import traceback
        log('error: %s\n%s' % (e, traceback.format_exc()), xbmc.LOGERROR)



HOME = None


def clear_health():
    import xbmcgui
    w = xbmcgui.Window(10000)
    for k in ('NF.Health', 'NF.HealthColor'):
        w.clearProperty(k)


def update_health(player):
    """Torrent health for the OSD: download speed / seeds, coloured by whether it keeps up with the video."""
    import json, urllib.request, urllib.parse, xbmcgui
    w = xbmcgui.Window(10000)
    try:
        if not player.isPlayingVideo():
            return clear_health() if w.getProperty('NF.Health') else None
        path = player.getPlayingFile()
        if not is_elementum(path):
            return clear_health() if w.getProperty('NF.Health') else None
        if not xbmc.getCondVisibility('Window.IsActive(videoosd) | Player.Paused | Player.Caching | Window.IsActive(seekbar)'):
            return
        data = json.loads(urllib.request.urlopen('http://127.0.0.1:65220/torrents/list', timeout=2).read().decode('utf-8'))
        if not data:
            return clear_health()
        fname = urllib.parse.unquote(path).rsplit('/', 1)[-1].lower()
        t = next((x for x in data if x.get('name', '').lower() in fname or fname in x.get('name', '').lower()), None)
        t = t or max(data, key=lambda x: x.get('download_rate', 0))
        rate = float(t.get('download_rate') or 0) * 1024  # bytes/s
        seeds = int(t.get('seeders') or 0)
        done = t.get('status') in ('Finished', 'Seeding')
        total = player.getTotalTime() or 0
        need = (float(t.get('size_bytes') or 0) / total) if total > 0 else 0  # average video bitrate, bytes/s
        if done:
            text, color = 'Buffered ahead  ·  %d seeds' % seeds, 'FF46D369'
        else:
            spd = '%.1f MB/s' % (rate / 1048576) if rate >= 1048576 else '%d KB/s' % (rate / 1024)
            text = '%s  ·  %d seeds' % (spd, seeds)
            ratio = rate / need if need else 0
            color = 'FF46D369' if ratio >= 1.5 else ('FFF5C518' if ratio >= 1.0 else 'FFE50914')
        w.setProperty('NF.Health', text)
        w.setProperty('NF.HealthColor', color)
    except Exception as e:
        log('health: %s' % e, xbmc.LOGDEBUG)



def reload_widgets_when_elementum_ready(monitor):
    """Home widgets ask Elementum for their lists right at startup, often before its server is up, and then
    stay empty. Wait for Elementum and bump Window(Home).Property(NF.WidgetReload) so the widgets reload."""
    import urllib.request
    import xbmcgui
    home = xbmcgui.Window(10000)
    home.setProperty('NF.WidgetWait', '1')   # keep the (still empty) rows visible so they can reload
    for _ in range(120):
        if monitor.abortRequested():
            return
        try:
            urllib.request.urlopen('http://127.0.0.1:65220/', timeout=2).read(1)
            break
        except Exception:
            if monitor.waitForAbort(1):
                return
    monitor.waitForAbort(1)
    home.setProperty('NF.WidgetReload', str(int(time.time())))
    log('Elementum is up - home widgets reloaded')
    monitor.waitForAbort(25)                  # give the lists time to load, then let empty rows hide again
    home.clearProperty('NF.WidgetWait')

state = {'token': 0}


class Player(xbmc.Player):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def onAVStarted(self):
        try:
            path = self.getPlayingFile()
        except Exception:
            return
        if not self.isPlayingVideo() or not is_elementum(path):
            return
        state['token'] += 1
        threading.Thread(target=run, args=(state['token'], self.monitor, self), daemon=True).start()

    def onPlayBackStopped(self):
        state['token'] += 1

    onPlayBackEnded = onPlayBackStopped


if __name__ == '__main__':
    monitor = xbmc.Monitor()
    player = Player(monitor)
    log('started')
    threading.Thread(target=reload_widgets_when_elementum_ready, args=(monitor,), daemon=True).start()
    try:  # service (re)started during an Elementum playback -> handle it now (unless subs are already loaded)
        if player.isPlayingVideo() and is_elementum(player.getPlayingFile()) and not (os.path.isdir(OUT) and os.listdir(OUT)):
            player.onAVStarted()
    except Exception:
        pass
    while not monitor.abortRequested():
        update_health(player)
        if monitor.waitForAbort(2):
            break
    clear_health()
