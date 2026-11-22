# NF boot priority (claude 20261115): let Elementum start first so the home rows fill quickly
def _nf_wait_for_elementum(limit=45):
    import socket, time, xbmc
    mon, t0 = xbmc.Monitor(), time.time()
    while time.time() - t0 < limit and not mon.abortRequested():
        try:
            socket.create_connection(('127.0.0.1', 65220), 1).close()
            return
        except Exception:
            mon.waitForAbort(1)
_nf_wait_for_elementum()
# /NF boot priority

# service.nf.skipintro - Netflix-style "Skip Intro" / "Skip Recap" button for TV episodes.
# Timestamps: introdb.app (community database, free anonymous API).
import json, re, threading, urllib.parse, urllib.request
import xbmc, xbmcaddon, xbmcgui
import nflogo

ADDON = xbmcaddon.Addon()
PATH = ADDON.getAddonInfo('path')
API = 'https://api.introdb.app/segments?imdb_id=%s&season=%s&episode=%s'
ACTION_SELECT = (7, 100, 79)          # select, left click, play/pause on some remotes
ACTION_BACK = (9, 10, 92, 110)        # parent dir, previous menu, back, nav back


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[SkipIntro] %s' % msg, level)


def http_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Kodi service.nf.skipintro'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))


def show_imdb(title, year=''):
    q = re.sub(r'[^a-z0-9 ]', '', title.lower()).strip().replace(' ', '_')
    if not q:
        return ''
    data = http_json('https://v2.sg.media-imdb.com/suggestion/%s/%s.json' % (q[0], urllib.parse.quote(q)))
    for r in data.get('d', []):
        if r.get('id', '').startswith('tt') and r.get('qid') in ('tvSeries', 'tvMiniSeries'):
            return r['id']
    return ''


class SkipDialog(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.target = kwargs.get('target', 0)
        self.label = kwargs.get('label', 'Skip Intro')
        self.result = None

    def onInit(self):
        self.setProperty('label', self.label)

    def onClick(self, control_id):
        if control_id == 3001:
            self.result = 'skip'
            self.close()

    def onAction(self, action):
        aid = action.getId()
        if aid in ACTION_BACK:
            self.result = 'dismiss'
            self.close()
        elif aid in ACTION_SELECT:
            self.result = 'skip'
            self.close()


class State:
    segments = []      # [(label, start, end)]
    dismissed = set()
    token = 0


def fetch(token):
    try:
        for _ in range(30):  # wait until the player publishes the episode info
            if xbmc.getInfoLabel('VideoPlayer.Episode'):
                break
            xbmc.sleep(500)
        season, episode = xbmc.getInfoLabel('VideoPlayer.Season'), xbmc.getInfoLabel('VideoPlayer.Episode')
        if not season or not episode:
            return
        title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle')
        imdb = show_imdb(title)
        if not imdb:
            log('no IMDb id for "%s"' % title)
            return
        data = http_json(API % (imdb, int(season), int(episode)))
        segs = []
        for key, label in (('recap', 'Skip Recap'), ('intro', 'Skip Intro')):
            s = data.get(key) or {}
            if s.get('end_sec'):
                segs.append((label, float(s.get('start_sec') or 0), float(s['end_sec'])))
        if token == State.token:
            State.segments = segs
        log('%s S%sE%s (%s): %s' % (title, season, episode, imdb, segs or 'no segments'))
    except Exception as e:
        log('lookup failed: %s' % e, xbmc.LOGWARNING)


class Player(xbmc.Player):
    def onAVStarted(self):
        State.token += 1
        State.segments, State.dismissed = [], set()
        nflogo.clear()
        if self.isPlayingVideo():
            threading.Thread(target=nflogo.lookup, args=(self, State.token, lambda: State.token), daemon=True).start()
            threading.Thread(target=fetch, args=(State.token,), daemon=True).start()

    def onPlayBackStopped(self):
        State.token += 1
        State.segments = []
        nflogo.clear()

    onPlayBackEnded = onPlayBackStopped


def main():
    monitor, player = xbmc.Monitor(), Player()
    log('started')
    while not monitor.abortRequested():
        if monitor.waitForAbort(0.5):
            break
        if not State.segments or not player.isPlayingVideo():
            continue
        try:
            t = player.getTime()
        except Exception:
            continue
        for label, start, end in State.segments:
            if label in State.dismissed or not (start - 0.5 <= t < end - 3):
                continue
            if xbmc.getCondVisibility('Window.IsActive(videoosd) | Player.Paused | Window.IsActive(seekbar) | !Window.IsActive(fullscreenvideo)'):
                continue
            State.dismissed.add(label)  # offer once per segment
            dlg = SkipDialog('skipintro.xml', PATH, 'default', '1080i', target=end, label=label)
            closer = threading.Thread(target=auto_close, args=(dlg, player, end, monitor), daemon=True)
            closer.start()
            dlg.doModal()
            if dlg.result == 'skip' and player.isPlayingVideo():
                player.seekTime(end)
                log('%s -> %.1fs' % (label, end))
            del dlg
            break


def auto_close(dlg, player, end, monitor):
    """Close the button when the segment is over (or after 15 s at most), like Netflix."""
    waited = 0
    while not monitor.abortRequested() and waited < 15:
        if monitor.waitForAbort(0.5):
            break
        waited += 0.5
        try:
            if not player.isPlayingVideo() or player.getTime() >= end - 1:
                break
        except Exception:
            break
    try:
        dlg.close()
    except Exception:
        pass


if __name__ == '__main__':
    main()
