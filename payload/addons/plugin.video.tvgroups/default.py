# -*- coding: utf-8 -*-
# Live TV tiles for the Home row: one square tile per channel group; a tile opens grid.py (8 square cards per row).
import sys, os, urllib.parse
import xbmc, xbmcgui, xbmcplugin, xbmcaddon
GROUPS = [('ראשי', 'Israel'), ('פולין', 'Poland'), ('חדשות', 'News'), ('ספורט', 'Sports'), ('סרטים', 'Movies'), ('ילדים', 'Kids'),
          ('מוזיקה', 'Music'), ('טבע ורגיעה', 'Nature & Relax'), ('טיולים ואופנה', 'Travel & Fashion')]
handle = int(sys.argv[1])
params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))
path = xbmcaddon.Addon().getAddonInfo('path')
if 'g' in params:  # run as a plain action (not a folder), so Kodi does not open the Videos/Files window first
    title = dict(GROUPS).get(params['g'], params['g'])
    xbmc.executebuiltin('RunScript(%s,%s,%s)' % (os.path.join(path, 'grid.py'), params['g'], title))
else:
    for i, (grp, label) in enumerate(GROUPS):
        art = os.path.join(path, 'resources', 'tiles', 't5_%d.png' % i)
        li = xbmcgui.ListItem(label, ' ')
        li.setArt({'icon': art, 'thumb': art, 'poster': art})
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(handle, '%s?g=%s' % (sys.argv[0], urllib.parse.quote(grp)), li, isFolder=False)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
