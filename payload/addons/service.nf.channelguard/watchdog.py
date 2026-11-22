# -*- coding: utf-8 -*-
# Stuck live channel: Kodi's audio clock sometimes jumps far ahead after quick channel changes
# ("ActiveAE - large audio sync error" over and over); the video then waits for it and the screen
# stays black. When that shows up in the log while a channel plays, the channel is reopened.
import json, os, threading, time, xbmc, xbmcvfs

LOG = xbmcvfs.translatePath('special://temp/kodi.log')

def rpc(method, params=None):
    return json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})))

class Watchdog(threading.Thread):
    def __init__(self, monitor, current):
        super().__init__(daemon=True)
        self.monitor, self.current = monitor, current   # current(): channel id playing, or None
        self.last_fix = 0

    def run(self):
        pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
        while not self.monitor.waitForAbort(3):
            try:
                size = os.path.getsize(LOG)
                if size < pos:
                    pos = 0   # log rotated
                with open(LOG, 'rb') as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
                pos = size
            except Exception:
                continue
            if chunk.count(b'large audio sync error') < 15:
                continue
            cid = self.current()
            if cid is None or time.time() - self.last_fix < 60 or not xbmc.Player().isPlayingVideo():
                continue
            self.last_fix = time.time()
            xbmc.log('channelguard: audio clock stuck, reopening channel %s' % cid, xbmc.LOGWARNING)
            xbmc.executebuiltin('Notification(הערוץ נתקע,פותח אותו מחדש,3000)')
            rpc('Player.Stop', {'playerid': 1})
            self.monitor.waitForAbort(0.8)
            rpc('Player.Open', {'item': {'channelid': cid}})
