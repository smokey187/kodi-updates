# service.subs.multilang/more.py - "Download more subtitles": next best results per language, with a small toast.
import json, os, sys, threading
import xbmc, xbmcgui

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import service as S  # noqa: E402  (shares search / download / naming code with the background service)

PER_LANG_MORE = 2


class Toast(xbmcgui.WindowXMLDialog):
    def onInit(self):
        pass

    def set(self, title, sub='', busy=True):
        self.setProperty('title', title)
        self.setProperty('sub', sub)
        self.setProperty('busy', '1' if busy else '')

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 110):
            self.close()


def player_json(method, **params):
    params.setdefault('playerid', 1)
    return json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))).get('result')


def main():
    player = xbmc.Player()
    if not player.isPlayingVideo():
        return
    toast = Toast('nf_toast.xml', HERE, 'default', '1080i')
    toast.show()
    toast.set('Looking for more subtitles', 'English  ·  Polish  ·  Hebrew')
    before = (player_json('Player.GetProperties', properties=['currentsubtitle', 'subtitleenabled']) or {})
    try:
        st = json.load(open(os.path.join(S.OUT, 'state.json')))
    except Exception:
        st = {'keys': []}
    have = set(st.get('keys', []))
    os.makedirs(S.OUT, exist_ok=True)
    from a4kSubtitles import api as a4kapi
    api = a4kapi.A4kSubtitlesApi()
    added = []
    try:
        all_results = api.search({'languages': ','.join(l for l, _ in S.LANGS), 'preferredlanguage': S.LANGS[0][0]}) or []
    except Exception as e:
        S.log('more: search failed: %s' % e, xbmc.LOGWARNING)
        all_results = []
    for lang, code in S.LANGS:
        toast.set('Looking for more subtitles', '%s…' % lang)
        results = [r for r in all_results if r.get('lang') == lang]
        if code == 'he':
            try:
                wz = S.wizdom_search(player.getPlayingFile().rsplit('/', 1)[-1])
            except Exception:
                wz = []
            results = sorted(wz + results, key=lambda r: (r.get('sync') != 'true', r.get('service_name') != 'wizdom'))
        n_existing = len([f for f in os.listdir(S.OUT) if f.endswith('.%s.srt' % code) or ('.%s.' % code) in f])
        got = 0
        for r in results:
            if got >= PER_LANG_MORE:
                break
            if S.key_of(r) in have:
                continue
            have.add(S.key_of(r))
            try:
                src = S.wizdom_download(r['_wizdom_id']) if r.get('_wizdom_id') else api.download(r)
            except Exception as e:
                S.log('more: download failed %s' % e, xbmc.LOGWARNING)
                continue
            if not src or not os.path.isfile(src):
                continue
            got += 1
            n = n_existing + got
            sync = r.get('sync') == 'true'
            ext = os.path.splitext(src)[1] or '.srt'
            dst = os.path.join(S.OUT, '%d %s%s.%s%s' % (n, 'sync ' if sync else '', S.safe(r.get('service_name', '')).replace('_', '-') or 'sub', code, ext))
            S.to_utf8(src, dst, code)
            S.remember_key(r)
            added.append((lang, dst))
    for lang, dst in added:
        player.setSubtitles(dst)
        xbmc.sleep(200)
    # keep the subtitle that was showing before
    cur = (before.get('currentsubtitle') or {}).get('index')
    if cur is not None and added:
        player_json('Player.SetSubtitle', subtitle=cur, enable=bool(before.get('subtitleenabled')))
    if added:
        langs = sorted({l for l, _ in added}, key=lambda l: [x for x, _ in S.LANGS].index(l))
        toast.set('Added %d subtitle%s' % (len(added), '' if len(added) == 1 else 's'), '  ·  '.join(langs), busy=False)
    else:
        toast.set('No more subtitles found', 'You already have the best ones', busy=False)
    xbmc.sleep(2600)
    toast.close()
    del toast
    if added:  # show them right away in the Audio & Subtitles panel
        xbmc.executebuiltin('ActivateWindow(videoosd)')
        xbmc.sleep(500)
        xbmc.executebuiltin('SetFocus(9201)')


main()
