# -*- coding: utf-8 -*-
# Live channel guard:
# - a channel that fails to open is remembered for a while, and its backup (" - גיבוי", " - גיבוי 2", ...)
#   is opened instead, now and the next times it is chosen;
# - the backup that worked is remembered per channel and opened first next time;
# - if no version of the channel works, it goes back to the channel that was playing.
import json, os, time, xbmc, xbmcgui, xbmcvfs
from checker import Checker
from watchdog import Watchdog

STATE = xbmcvfs.translatePath('special://profile/addon_data/service.nf.channelguard/state.json')
FAIL_TTL = 6 * 3600
ORDER = ['', 'גיבוי', 'גיבוי 2', 'גיבוי 3', 'לקויי שמיעה']
# channels blocked outside Israel at times (rights): any failure of these is reported as such
GEO_BASES = {'כאן 11'}

def rpc(method, params=None):
    return json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})))

def notify(title, text):
    xbmc.executebuiltin('Notification(%s,%s,5000,DefaultIconError.png)' % (title.replace(',', ' '), text.replace(',', ' ')))

class Guard(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.good = None
        self.trying = None
        self.since = 0
        self.hops = 0
        self.redirecting = False
        self.queue = []
        self.fullscreen_until = 0
        self.last_fail = 0
        self.pending = None
        self.names, self.family = {}, {}
        self.loaded = 0
        try:
            with open(STATE) as f:
                st = json.load(f)
        except Exception:
            st = {}
        self.preferred = st.get('preferred', {})
        self.failed = {int(k): v for k, v in st.get('failed', {}).items()}

    def save(self):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, 'w') as f:
                json.dump({'preferred': self.preferred, 'failed': self.failed}, f, ensure_ascii=False)
        except Exception as e:
            xbmc.log('channelguard: save failed %s' % e, xbmc.LOGWARNING)

    def load_channels(self):
        if time.time() - self.loaded < 3600 and self.names:
            return
        try:
            chans = rpc('PVR.GetChannels', {'channelgroupid': 'alltv'})['result']['channels']
        except Exception:
            return
        self.names = {c['channelid']: c['label'] for c in chans}
        fam = {}
        for cid, name in self.names.items():
            base, _, tail = name.partition(' - ')
            fam.setdefault(base.strip(), []).append((ORDER.index(tail.strip()) if tail.strip() in ORDER else 9, cid))
        self.family = {cid: [c for _, c in sorted(v)] for v in fam.values() for _, cid in v}
        self.loaded = time.time()

    def base(self, cid):
        return self.names.get(cid, '').partition(' - ')[0].strip()

    def is_failed(self, cid):
        t = self.failed.get(cid)
        return t is not None and time.time() - t < FAIL_TTL

    def alternative(self, cid):
        """the version of cid's channel to open instead: the remembered one, else the next that hasn't failed"""
        pref = self.preferred.get(self.base(cid))
        if pref is not None and pref != cid and pref in self.names and not self.is_failed(pref):
            return pref
        for c in self.family.get(cid, []):
            if c != cid and not self.is_failed(c):
                return c
        return None

    def open(self, cid):
        self.hops += 1
        self.redirecting = True
        rpc('Player.Open', {'item': {'channelid': cid}})

    def onNotification(self, sender, method, data):
        if method in ('Player.OnPlay', 'Player.OnAVStart', 'Player.OnStop'):
            self.queue.append((method, data))
        # a live channel goes to full screen as soon as it is chosen, not after it has started in the
        # small background video window of the home screen
        if method == 'Player.OnPlay' and '"type": "channel"' in (data or '').replace('"type":"channel"', '"type": "channel"'):
            self.fullscreen_until = time.time() + 15

    def push_fullscreen(self):
        """full screen as soon as Kodi allows it (not while the 'working' dialog of the stream lookup is up)"""
        if time.time() > self.fullscreen_until:
            return False
        if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
            self.fullscreen_until = 0
            return False
        if xbmc.getCondVisibility('Player.HasVideo') and not xbmc.getCondVisibility('System.HasActiveModalDialog'):
            xbmc.log('channelguard: full screen', xbmc.LOGINFO)
            xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
        return True

    def close_failed_dialog(self):
        """Kodi's own 'Playback failed' box: the guard already said what happened and what it did"""
        if time.time() - self.last_fail > 60 or not xbmc.getCondVisibility('Window.IsActive(okdialog)'):
            return
        head = xbmc.getInfoLabel('Control.GetLabel(1)')
        if head in ('Playback failed', xbmc.getLocalizedString(16026)):
            xbmc.executebuiltin('Dialog.Close(okdialog,true)')

    def handle(self, method, data):
        try:
            d = json.loads(data) if data else {}
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        item = d.get('item') or {}
        cid = item.get('id') if item.get('type') == 'channel' else None
        if cid is None:
            return
        self.load_channels()
        if method == 'Player.OnPlay':
            self.pending = None
            if self.redirecting:
                self.redirecting = False
            else:
                self.hops = 0
            self.trying, self.since = cid, time.time()
            # chosen again while it is known to fail: open the version that works straight away
            if self.is_failed(cid) and self.hops < 4:
                alt = self.alternative(cid)
                if alt is not None:
                    self.trying = None
                    self.open(alt)
        elif method == 'Player.OnAVStart':
            xbmcgui.Window(10000).setProperty('NF.Live.%d' % cid, 'ok')
            self.good, self.trying, self.hops = cid, None, 0
            self.preferred[self.base(cid)] = cid
            self.failed.pop(cid, None)
            self.save()
        elif method == 'Player.OnStop':
            if cid != self.trying or time.time() - self.since > 30:
                return
            # a stop before the picture came is a failure only if nothing else is chosen right after
            # it: zapping on quickly also stops the channel that was still opening
            self.trying = None
            self.pending = (cid, time.time())

    def check_pending(self):
        if not self.pending or time.time() - self.pending[1] < 2.5:
            return
        cid, _ = self.pending
        self.pending = None
        if self.trying is not None or xbmc.Player().isPlaying():
            return   # something else was chosen and is opening or playing
        self.fail(cid)

    def fail(self, cid):
        if True:
            self.last_fail = time.time()
            home = xbmcgui.Window(10000)
            geo = home.getProperty('NF.Live.%d' % cid) == 'geo' or self.base(cid) in GEO_BASES
            if not geo:
                home.setProperty('NF.Live.%d' % cid, 'fail')
            self.failed[cid] = time.time()
            if self.preferred.get(self.base(cid)) == cid:
                self.preferred.pop(self.base(cid), None)
            self.save()
            name = self.names.get(cid, 'הערוץ')
            alt = self.alternative(cid) if self.hops < 4 else None
            if alt is not None and home.getProperty('NF.Live.%d' % alt) == 'geo':
                alt = None
            if geo:
                if self.good is not None and self.good != cid:
                    notify('%s זמין כרגע רק בישראל' % self.base(cid), 'חוזר ל%s' % self.names.get(self.good, ''))
                    self.open(self.good)
                else:
                    notify('%s זמין כרגע רק בישראל' % self.base(cid), 'שידור עם זכויות לישראל בלבד')
                return
            if alt is not None:
                notify('%s לא זמין' % name, 'עובר ל%s' % self.names.get(alt, 'גיבוי'))
                self.open(alt)
            elif self.good is not None and self.good != cid and self.hops < 5:
                notify('%s לא זמין כרגע' % self.base(cid), 'חוזר ל%s' % self.names.get(self.good, ''))
                self.open(self.good)
            else:
                notify('%s לא זמין כרגע' % self.base(cid), 'אין גיבוי שעובד')

if __name__ == '__main__':
    m = Guard()
    m.load_channels()
    Checker(m, lambda: (m.load_channels(), m.names)[1]).start()
    Watchdog(m, lambda: m.good).start()
    while not m.abortRequested():
        while m.queue:
            method, data = m.queue.pop(0)
            try:
                m.handle(method, data)
            except Exception as e:
                xbmc.log('channelguard: %s' % e, xbmc.LOGWARNING)
        busy = m.push_fullscreen()
        m.check_pending()
        m.close_failed_dialog()
        if m.waitForAbort(0.05 if busy else 0.2):
            break
