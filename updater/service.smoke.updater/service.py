# Smoke Box Updater - pulls box customizations from GitHub and applies them safely.
#
# Flow: every few hours fetch manifest.json -> find files whose sha256 differs from the box
# (new version, or an add-on update overwrote a patch) -> download + verify to a staging dir ->
# wait until nothing plays and the remote was idle for a while -> back up -> swap files in ->
# restart Kodi -> after restart check health (skin still loaded, no Python traceback in the
# updated files) -> keep, or roll back and mark the version bad.
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

import xbmc
import xbmcgui

REPO = 'https://raw.githubusercontent.com/smokey187/kodi-updates/main/'
KODI = '/storage/.kodi'
DATA = os.path.join(KODI, 'userdata', 'addon_data', 'service.smoke.updater')
STATE = os.path.join(DATA, 'state.json')
STAGING = os.path.join(DATA, 'staging')
BACKUPS = os.path.join(DATA, 'backups')
LOG = os.path.join(KODI, 'temp', 'kodi.log')

FIRST_CHECK = 120              # seconds after Kodi start
INTERVAL = 3 * 3600            # between checks
JITTER = 1800
IDLE_NEEDED = 10 * 60          # remote untouched this long before applying
HEALTH_WAIT = 60               # after the restart, before judging the update
KEEP_BACKUPS = 3
# skin settings that belong to whoever uses the box (search history...): never taken from the repo
PERSONAL_FILES = ('userdata/addon_data/skin.arctic.fuse/settings.xml',)
PERSONAL_ID = re.compile(r'history', re.I)
SETTING = re.compile(r'(<setting id="([^"]+)"[^>]*?)(?:/>|>([^<]*)</setting>)')
if os.path.exists(os.path.join(DATA, 'fast')):  # testing: touch addon_data/service.smoke.updater/fast
    FIRST_CHECK, IDLE_NEEDED, HEALTH_WAIT = 20, 0, 30


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[smoke.updater] %s' % msg, level)


def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, ms=5000):
    try:
        xbmcgui.Dialog().notification('Box update', msg, icon, ms)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {'version': 0, 'bad': [], 'pending': None}


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


def fetch(path, timeout=30):
    url = REPO + urllib.parse.quote(path) + '?t=%d' % int(time.time())
    req = urllib.request.Request(url, headers={'User-Agent': 'smoke-updater', 'Cache-Control': 'no-cache'})
    return urllib.request.urlopen(req, timeout=timeout).read()


def sha256_file(p):
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
    except (IOError, OSError):
        return None
    return h.hexdigest()


def addon_version(addon_id):
    try:
        with open(os.path.join(KODI, 'addons', addon_id, 'addon.xml'), encoding='utf-8', errors='ignore') as f:
            m = re.search(r'<addon\b[^>]*\bversion="([^"]+)"', f.read())
            return m.group(1) if m else None
    except (IOError, OSError):
        return None


def addon_of(path):
    parts = path.split('/')
    return parts[1] if len(parts) > 2 and parts[0] == 'addons' else None


def keep_personal(path, new_bytes):
    """Repo version of a settings file, but with this box's own values for personal settings."""
    if path not in PERSONAL_FILES:
        return new_bytes
    try:
        local = open(os.path.join(KODI, path), encoding='utf-8', errors='ignore').read()
    except (IOError, OSError):
        local = ''
    mine = {m.group(2): m.group(0) for m in SETTING.finditer(local) if PERSONAL_ID.search(m.group(2))}
    text = new_bytes.decode('utf-8', errors='ignore')

    def swap(m):
        if not PERSONAL_ID.search(m.group(2)):
            return m.group(0)
        return mine.get(m.group(2), '%s></setting>' % m.group(1))
    return SETTING.sub(swap, text).encode('utf-8')


def needed_files(man, st):
    """Files to (re)write, skipping add-ons whose installed version is not the one the patch was made for.

    'once' files (settings Kodi rewrites itself, generated skin includes) are only written when the
    repo changed them, never just because they drifted on the box - otherwise every check would
    rewrite them and restart Kodi.
    """
    req = man.get('requires', {})
    applied = st.get('applied', {})
    skipped, out = set(), []
    for path, meta in man['files'].items():
        aid = addon_of(path)
        if aid in req:
            have = addon_version(aid)
            if have != req[aid]:
                skipped.add('%s (box %s, patch for %s)' % (aid, have, req[aid]))
                continue
        if meta.get('once'):
            if applied.get(path) != meta['sha256'] and sha256_file(os.path.join(KODI, path)) != meta['sha256']:
                out.append(path)
        elif sha256_file(os.path.join(KODI, path)) != meta['sha256']:
            out.append(path)
    for s in sorted(skipped):
        log('skipping %s' % s, xbmc.LOGWARNING)
    return out


def wait_idle(monitor, player):
    while not monitor.abortRequested():
        if not player.isPlaying() and xbmc.getGlobalIdleTime() >= IDLE_NEEDED:
            return True
        if monitor.waitForAbort(60):
            return False
    return False


def restart_kodi():
    log('restarting Kodi')
    xbmc.sleep(1500)
    subprocess.Popen(['systemctl', 'restart', 'kodi'])


def enable_addons(ids):
    for aid in ids:
        xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.SetAddonEnabled',
                                        'params': {'addonid': aid, 'enabled': True}}))


def check(monitor, player):
    st = load_state()
    man = json.loads(fetch('manifest.json').decode('utf-8'))
    ver = int(man['version'])
    if ver in st.get('bad', []):
        return
    todo = needed_files(man, st)
    if not todo:
        if st.get('version') != ver:
            st['version'] = ver
            st['applied'] = {p: m['sha256'] for p, m in man['files'].items() if m.get('once')}
            save_state(st)
        run_actions(man, st, monitor, player)
        return
    log('version %d: %d file(s) to update' % (ver, len(todo)))

    stage = os.path.join(STAGING, str(ver))
    shutil.rmtree(stage, ignore_errors=True)
    for path in todo:
        data = fetch('payload/' + path, timeout=120)
        if hashlib.sha256(data).hexdigest() != man['files'][path]['sha256']:
            raise ValueError('checksum mismatch for %s' % path)
        dst = os.path.join(stage, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(keep_personal(path, data))

    if not wait_idle(monitor, player):
        return
    if needed_files(man, load_state()) != todo:  # something changed while we waited: start over next time
        return

    bdir = os.path.join(BACKUPS, '%d-%s' % (ver, time.strftime('%Y%m%d-%H%M%S')))
    created = []
    for path in todo:
        target = os.path.join(KODI, path)
        if os.path.exists(target):
            b = os.path.join(bdir, path)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(target, b)
        else:
            created.append(path)
    st['pending'] = {'version': ver, 'backup': bdir, 'files': todo, 'created': created,
                     'skin': xbmc.getSkinDir(), 'enable': man.get('enable', []),
                     'once': {p: m['sha256'] for p, m in man['files'].items() if m.get('once')}}
    save_state(st)
    for path in todo:
        target = os.path.join(KODI, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(os.path.join(stage, path), target)
    shutil.rmtree(stage, ignore_errors=True)
    notify('Installing update %d - restarting' % ver)
    restart_kodi()


def run_actions(man, st, monitor, player):
    """One-time shell actions from the repo (e.g. cleanup), each run once per box, only when idle.

    They run through systemd-run so they survive the script stopping/starting Kodi."""
    for act in man.get('actions', []):
        aid = act['id']
        if aid in st.get('done', []):
            continue
        data = fetch(act['script'], timeout=60)
        if hashlib.sha256(data).hexdigest() != act['sha256']:
            raise ValueError('checksum mismatch for action %s' % aid)
        if not wait_idle(monitor, player):
            return
        path = os.path.join(DATA, 'action-%s.sh' % re.sub(r'[^A-Za-z0-9_-]', '', aid))
        with open(path, 'wb') as f:
            f.write(data)
        st.setdefault('done', []).append(aid)  # recorded first: never run twice, even if it kills Kodi
        save_state(st)
        log('running action %s' % aid)
        subprocess.Popen(['systemd-run', '--unit=smoke-action-%s-%d' % (re.sub(r'[^A-Za-z0-9-]', '', aid), int(time.time())),
                          '/bin/sh', path])
        return  # one action per check


def broken_after_update(pending):
    if pending.get('skin') and xbmc.getSkinDir() != pending['skin']:
        return 'skin %s did not load (now %s)' % (pending['skin'], xbmc.getSkinDir())
    try:
        with open(LOG, encoding='utf-8', errors='ignore') as f:
            text = f.read()
    except (IOError, OSError):
        return None
    for path in pending['files']:
        if path.endswith('.py') and ('File "%s/%s"' % (KODI, path)) in text:
            return 'Python error in %s' % path
    return None


def rollback(pending, st, why):
    log('rolling back version %d: %s' % (pending['version'], why), xbmc.LOGERROR)
    for path in pending['files']:
        b = os.path.join(pending['backup'], path)
        if os.path.exists(b):
            shutil.copy2(b, os.path.join(KODI, path))
    for path in pending['created']:
        try:
            os.remove(os.path.join(KODI, path))
        except OSError:
            pass
    st.setdefault('bad', []).append(pending['version'])
    st['pending'] = None
    save_state(st)
    notify('Update failed - restored previous version', xbmcgui.NOTIFICATION_ERROR, 8000)
    restart_kodi()


def prune_backups():
    try:
        dirs = sorted(os.listdir(BACKUPS))
        for d in dirs[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(BACKUPS, d), ignore_errors=True)
    except OSError:
        pass


def verify_pending(monitor):
    st = load_state()
    p = st.get('pending')
    if not p:
        return
    if monitor.waitForAbort(HEALTH_WAIT):
        return
    why = broken_after_update(p)
    if why:
        rollback(p, st, why)
        return
    if p.get('enable'):
        enable_addons(p['enable'])
    st['version'] = p['version']
    st['applied'] = p.get('once', {})
    st['pending'] = None
    save_state(st)
    prune_backups()
    log('version %d installed OK' % p['version'])
    notify('Updated to version %d' % p['version'])


def main():
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    verify_pending(monitor)
    wait = FIRST_CHECK
    while not monitor.waitForAbort(wait):
        if load_state().get('pending'):  # a restart is on its way
            wait = 60
            continue
        try:
            check(monitor, player)
        except Exception as e:
            log('check failed: %s' % e, xbmc.LOGWARNING)
        wait = INTERVAL + random.randint(0, JITTER)


if __name__ == '__main__':
    main()
