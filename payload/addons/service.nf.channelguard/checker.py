# -*- coding: utf-8 -*-
# Light availability check of the live channels: fetch each stream's playlist (a few KB) the way
# inputstream.adaptive would, and publish the result as Window(Home).Property(NF.Live.<channelid>):
# ok, fail, or geo (HTTP 403: blocked outside Israel). Channels opened through a plugin are not
# fetched here; they get their state from real playback (service.py).
import io, threading, time, urllib.request, urllib.parse, xbmc, xbmcgui, xbmcvfs

M3U = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.idanplus/idanplus.m3u')
EVERY = 600

def streams():
    """{channel name: (url, headers)} for the channels with a direct stream in the m3u"""
    out, props = {}, {}
    try:
        lines = io.open(M3U, encoding='utf-8').read().splitlines()
    except Exception:
        return out
    for i, l in enumerate(lines):
        if l.startswith('#KODIPROP:inputstream.adaptive.manifest_headers='):
            props = dict(urllib.parse.parse_qsl(l.split('=', 1)[1]))
        elif l.startswith('#EXTINF') and i + 1 < len(lines):
            url = lines[i + 1].strip()
            if url.startswith('http'):
                out[l.rsplit(',', 1)[-1].strip()] = (url, props)
            props = {}
    return out

def probe(url, headers):
    h = {'User-Agent': headers.get('User-Agent', 'Mozilla/5.0')}
    if headers.get('Referer'):
        h['Referer'] = headers['Referer']
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=8) as r:
            r.read(2048)
            return 'ok'
    except urllib.error.HTTPError as e:
        return 'geo' if e.code == 403 else 'fail'
    except Exception:
        return 'fail'

class Checker(threading.Thread):
    def __init__(self, monitor, names):
        super().__init__(daemon=True)
        self.monitor, self.names = monitor, names   # names(): {channelid: name}
        self.home = xbmcgui.Window(10000)

    def run(self):
        if self.monitor.waitForAbort(20):
            return
        while not self.monitor.abortRequested():
            by_name = {n: c for c, n in self.names().items()}
            for name, (url, hdr) in streams().items():
                cid = by_name.get(name)
                if cid is None:
                    continue
                state = probe(url, hdr)
                self.home.setProperty('NF.Live.%d' % cid, state)
                if self.monitor.waitForAbort(1):   # spread the requests out
                    return
            if self.monitor.waitForAbort(EVERY):
                return
