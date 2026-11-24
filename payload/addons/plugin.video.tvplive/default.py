# -*- coding: utf-8 -*-
# Resolves a fresh official TVP live stream (TVP Sport by default) through TVP's own API each time it is opened.
import sys, json, urllib.request, urllib.parse
import xbmc, xbmcgui, xbmcplugin

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'

def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode('utf-8'))

def resolve(code):
    data = get_json('https://tvp.pl/api/tvp-stream/stream/data?station_code=' + urllib.parse.quote(code))['data']
    tok = get_json(data['stream_url'])
    for f in tok.get('formats', []):
        if f.get('mimeType') == 'application/x-mpegurl':
            return f['url']
    raise Exception('no HLS format for ' + code)

handle = int(sys.argv[1])
params = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))
code = params.get('code', 'KSP')
try:
    url = resolve(code)
    li = xbmcgui.ListItem(path=url + '|User-Agent=' + urllib.parse.quote(UA))
    li.setProperty('inputstream', 'inputstream.adaptive')
    li.setMimeType('application/vnd.apple.mpegurl')
    li.setContentLookup(False)
    xbmcplugin.setResolvedUrl(handle, True, li)
except Exception as e:
    xbmc.log('[tvplive] %s: %s' % (code, e), xbmc.LOGERROR)
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
