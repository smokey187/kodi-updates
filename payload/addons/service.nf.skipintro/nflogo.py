# Clear logo for the pause screen: when the playing item has no clearlogo art
# (Elementum streams), look it up on TMDb and publish Window(Home).Property(NF.PauseLogo).
import json, os, re, urllib.parse, urllib.request
import xbmc, xbmcaddon, xbmcgui

HOME = xbmcgui.Window(10000)
PROP = 'NF.PauseLogo'


def _key():
    kf = os.path.join(xbmcaddon.Addon('plugin.video.themoviedb.helper').getAddonInfo('path'),
                      'resources', 'tmdbhelper', 'lib', 'api', 'api_keys', 'tmdb.py')
    return re.search(r"API_KEY\s*=\s*['\"]([0-9a-f]{32})['\"]", open(kf).read()).group(1)


def _get(path, **q):
    q['api_key'] = _key()
    url = 'https://api.themoviedb.org/3/%s?%s' % (path, urllib.parse.urlencode(q))
    req = urllib.request.Request(url, headers={'User-Agent': 'Kodi'})
    return json.loads(urllib.request.urlopen(req, timeout=8).read().decode('utf-8'))


def clear():
    HOME.clearProperty(PROP)


def lookup(player, token, current):
    try:
        for _ in range(30):
            if xbmc.getInfoLabel('VideoPlayer.Title'):
                break
            xbmc.sleep(500)
        if xbmc.getInfoLabel('Player.Art(clearlogo)') or xbmc.getInfoLabel('Player.Art(tvshow.clearlogo)'):
            return
        if xbmc.getCondVisibility('VideoPlayer.Content(livetv)'):
            return
        show = xbmc.getInfoLabel('VideoPlayer.TVShowTitle')
        kind = 'tv' if show else 'movie'
        tmdb = ''
        try:
            tag = player.getVideoInfoTag()
            tmdb = tag.getUniqueID('tmdb') if not show else (tag.getUniqueID('tvshow.tmdb') or '')
        except Exception:
            pass
        if not tmdb:
            q = {'query': show or xbmc.getInfoLabel('VideoPlayer.Title')}
            y = xbmc.getInfoLabel('VideoPlayer.Year')
            if y and not show:
                q['year'] = y
            res = _get('search/%s' % kind, **q).get('results') or []
            if not res:
                return
            tmdb = res[0]['id']
        logos = _get('%s/%s/images' % (kind, tmdb), include_image_language='en,null').get('logos') or []
        logos.sort(key=lambda l: (l.get('iso_639_1') != 'en', l.get('file_path', '').endswith('.svg')))
        if logos and token == current():
            HOME.setProperty(PROP, 'https://image.tmdb.org/t/p/w500' + logos[0]['file_path'])
            xbmc.log('[NFLogo] %s %s -> logo' % (kind, tmdb), xbmc.LOGINFO)
    except Exception as e:
        xbmc.log('[NFLogo] lookup failed: %s' % e, xbmc.LOGWARNING)
