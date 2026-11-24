# Smoke Box Updater 1.4 - pulls box customizations from GitHub and installs them safely.
#
# When:  right after the box starts (big "updating" screen, before anyone watches), or later when a
#        check (every 30 min, also while watching) finds a new version: it downloads in the background
#        and installs as soon as nothing is playing and the remote was idle for a few minutes
#        (30 s countdown on screen, Back = later). "Box update" in the side menu installs right away.
# How:   download + sha256 check -> back up -> swap files -> restart Kodi -> health check
#        (skin loaded, no Python error in the new files) -> one-time actions (after Live TV loaded)
#        -> "Update complete" screen + report to the Telegram channel. Broken -> roll back + report.
import base64
import hashlib
import json
import os
import re
import shutil
import socket
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
ACTIONS = os.path.join(DATA, 'actions')
LOG = os.path.join(KODI, 'temp', 'kodi.log')

BOOT_CHECK = 15                # seconds after Kodi start
BOOT_RETRIES = 4               # network may still be coming up
CHECK_EVERY = 30 * 60
IDLE_NEEDED = 5 * 60           # nothing playing + remote untouched this long -> install
COUNTDOWN = 30
POSTPONE = 30 * 60             # Back pressed during the countdown
HEALTH_WAIT = 45
ACTION_TIMEOUT = 10 * 60
KEEP_BACKUPS = 3
PERSONAL_FILES = ('userdata/addon_data/skin.arctic.fuse/settings.xml',)
PERSONAL_ID = re.compile(r'history', re.I)
SETTING = re.compile(r'(<setting id="([^"]+)"[^>]*?)(?:/>|>([^<]*)</setting>)')
if os.path.exists(os.path.join(DATA, 'fast')):  # testing: touch addon_data/service.smoke.updater/fast
    IDLE_NEEDED, HEALTH_WAIT, CHECK_EVERY = 20, 20, 120

HOME = xbmcgui.Window(10000)
BASE = {'url': REPO}
READY = {}       # downloaded update waiting for the box to be idle
FIRST = {'on': False}


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[smoke.updater] %s' % msg, level)


def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, ms=5000):
    try:
        xbmcgui.Dialog().notification('Box update', msg, icon, ms)
    except Exception:
        pass


class Progress(object):
    def __init__(self, title, foreground=True):
        self.title, self.fg = title, foreground
        self.d = xbmcgui.DialogProgress() if foreground else xbmcgui.DialogProgressBG()
        self.d.create(title, '')

    def update(self, pct, msg):
        pct = max(0, min(100, int(pct)))
        if self.fg:
            self.d.update(pct, msg)
        else:
            self.d.update(pct, self.title, msg)

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass


# ---------------------------------------------------------------- state

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


def status(state, label, remote=None):
    """state: uptodate | ready | installing | failed | checking"""
    HOME.setProperty('SmokeUpdate.State', state)
    if state in ('ready', 'installing', 'failed', 'checking'):
        HOME.setProperty('SmokeUpdate.Show', '1')
    else:
        HOME.clearProperty('SmokeUpdate.Show')
    HOME.setProperty('SmokeUpdate.Label', label)
    HOME.setProperty('SmokeUpdate.Local', str(load_state().get('version', 0)))
    if remote is not None:
        HOME.setProperty('SmokeUpdate.Remote', str(remote))


# ---------------------------------------------------------------- Telegram report

def box_name():
    name = xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
    mac = ''
    for nic in ('eth0', 'wlan0'):
        try:
            mac = open('/sys/class/net/%s/address' % nic).read().strip()[-5:].replace(':', '')
            if mac and mac != '0000':
                break
        except (IOError, OSError):
            pass
    return '%s [%s]' % (name, mac or socket.gethostname())


def tg_token(obf):
    key = b'smoke-box-updater'
    raw = base64.b64decode(obf[::-1])
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode()


def report(text):
    st = load_state()
    st.setdefault('outbox', []).append('%s %s\n%s' % (time.strftime('%d.%m %H:%M'), box_name(), text))
    st['outbox'] = st['outbox'][-20:]
    save_state(st)
    flush_outbox()


def flush_outbox():
    st = load_state()
    tg = st.get('tg') or {}
    if not st.get('outbox') or not tg.get('token') or not tg.get('chat'):
        return
    try:
        token = tg_token(tg['token'])
    except Exception:
        return
    sent = 0
    for msg in st['outbox']:
        try:
            body = urllib.parse.urlencode({'chat_id': tg['chat'], 'text': msg, 'disable_web_page_preview': 'true'}).encode()
            urllib.request.urlopen('https://api.telegram.org/bot%s/sendMessage' % token, body, timeout=20).read()
            sent += 1
        except Exception as e:
            log('telegram: %s' % e, xbmc.LOGWARNING)
            break
    if sent:
        st = load_state()
        st['outbox'] = st.get('outbox', [])[sent:]
        save_state(st)


# ---------------------------------------------------------------- repo

def resolve_base():
    """Read the newest commit by its id: raw.githubusercontent.com caches 'main' for up to 5 minutes."""
    try:
        req = urllib.request.Request('https://api.github.com/repos/smokey187/kodi-updates/commits/main',
                                     headers={'User-Agent': 'smoke-updater', 'Accept': 'application/vnd.github.sha'})
        sha = urllib.request.urlopen(req, timeout=15).read().decode().strip()
        if re.match(r'^[0-9a-f]{40}$', sha):
            BASE['url'] = 'https://raw.githubusercontent.com/smokey187/kodi-updates/%s/' % sha
            return sha
    except Exception as e:
        log('commit lookup failed, using main: %s' % e, xbmc.LOGWARNING)
    BASE['url'] = REPO
    return None


def fetch(path, timeout=30):
    url = BASE['url'] + urllib.parse.quote(path) + '?t=%d' % int(time.time())
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
    return sorted(out)


def needed_actions(man, st):
    done = set(st.get('done', []))
    return [a for a in man.get('actions', []) if a['id'] not in done]


def restart_kodi():
    log('restarting Kodi')
    xbmc.sleep(1500)
    subprocess.Popen(['systemctl', 'restart', 'kodi'])


def rpc(method, **params):
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
    except Exception:
        return {}


def enable_addons(ids):
    for aid in ids:
        rpc('Addons.SetAddonEnabled', addonid=aid, enabled=True)


# ---------------------------------------------------------------- check + download

def check(monitor, foreground):
    """Look for a new version. Returns 'uptodate', 'ready' (downloaded, waiting), or raises."""
    resolve_base()
    st = load_state()
    man = json.loads(fetch('manifest.json').decode('utf-8'))
    ver = int(man['version'])
    if man.get('telegram') and st.get('tg') != man['telegram']:
        st['tg'] = man['telegram']
        save_state(st)
    flush_outbox()
    if ver in st.get('bad', []):
        status('uptodate', 'Up to date · v%d' % st.get('version', 0), ver)
        return 'uptodate'
    todo = needed_files(man, st)
    acts = needed_actions(man, st)
    if not todo and not acts:
        if st.get('version') != ver:
            st['version'] = ver
            st['applied'] = {p: m['sha256'] for p, m in man['files'].items() if m.get('once')}
            save_state(st)
        status('uptodate', 'Up to date · v%d' % ver, ver)
        READY.clear()
        return 'uptodate'
    if READY.get('ver') == ver and READY.get('todo') == todo and READY.get('acts') == [a['id'] for a in acts]:
        return 'ready'
    log('version %d: %d file(s), %d action(s)' % (ver, len(todo), len(acts)))
    stage = os.path.join(STAGING, str(ver))
    shutil.rmtree(stage, ignore_errors=True)
    prog = Progress('Setting up your box' if FIRST['on'] else 'Box update v%d' % ver, foreground) if foreground else None
    total = sum(man['files'][p].get('size', 1) for p in todo) or 1
    done = 0
    try:
        for i, path in enumerate(todo):
            if prog:
                pct = 60.0 * done / total
                prog.update(pct, 'Step 1 of 3 - downloading…  %d%%   (%d of %d files)' % (pct / 0.6, i + 1, len(todo)))
            data = fetch('payload/' + path, timeout=120)
            if hashlib.sha256(data).hexdigest() != man['files'][path]['sha256']:
                raise ValueError('checksum mismatch for %s' % path)
            dst = os.path.join(stage, path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(keep_personal(path, data))
            done += man['files'][path].get('size', 1)
        for a in acts:
            data = fetch(a['script'], timeout=60)
            if hashlib.sha256(data).hexdigest() != a['sha256']:
                raise ValueError('checksum mismatch for action %s' % a['id'])
            dst = os.path.join(stage, '_actions', a['id'] + '.sh')
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(data)
    finally:
        if prog:
            prog.close()
    READY.clear()
    READY.update({'ver': ver, 'man': man, 'todo': todo, 'acts': [a['id'] for a in acts], 'stage': stage})
    status('ready', 'Update ready · v%d' % ver, ver)
    return 'ready'


# ---------------------------------------------------------------- install

def install():
    """Swap the downloaded files in (foreground screen). Restarts Kodi when files changed."""
    ver, man, todo, stage = READY['ver'], READY['man'], READY['todo'], READY['stage']
    st = load_state()
    if needed_files(man, st) != todo:  # something changed meanwhile: download again on the next check
        READY.clear()
        return
    prog = Progress('Box update v%d' % ver)
    prog.update(62, 'Step 2 of 3 - saving a backup of the current version…')
    os.makedirs(ACTIONS, exist_ok=True)
    for aid in READY['acts']:
        shutil.copy2(os.path.join(stage, '_actions', aid + '.sh'), os.path.join(ACTIONS, aid + '.sh'))
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
    st['pending'] = {'version': ver, 'from': st.get('version', 0), 'backup': bdir, 'files': todo, 'created': created,
                     'skin': xbmc.getSkinDir(), 'enable': man.get('enable', []), 'actions': READY['acts'],
                     'once': {p: m['sha256'] for p, m in man['files'].items() if m.get('once')}}
    save_state(st)
    if todo:
        prog.update(75, 'Step 2 of 3 - installing %d files…' % len(todo))
        for path in todo:
            target = os.path.join(KODI, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(os.path.join(stage, path), target)
    shutil.rmtree(stage, ignore_errors=True)
    READY.clear()
    status('installing', 'Installing v%d…' % ver, ver)
    if todo:
        prog.update(85, 'Restarting Kodi - the update continues right after it starts')
        xbmc.sleep(2500)
        prog.close()
        restart_kodi()
        return
    prog.close()
    verify_pending(xbmc.Monitor(), restarted=False)


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
    status('failed', 'Update failed · back on v%d' % st.get('version', 0))
    report('❌ v%d FAILED - %s\nRestored v%d.' % (pending['version'], why, st.get('version', 0)))
    xbmcgui.Dialog().ok('Box update', 'The update to version %d did not work, so the box went back to version %d.[CR]Nothing is lost - Kodi restarts now.'
                        % (pending['version'], st.get('version', 0)))
    restart_kodi()


def prune_backups():
    try:
        dirs = sorted(os.listdir(BACKUPS))
        for d in dirs[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(BACKUPS, d), ignore_errors=True)
    except OSError:
        pass


def verify_pending(monitor, restarted=True):
    st = load_state()
    p = st.get('pending')
    if not p:
        return
    if restarted:
        prog = Progress('Box update v%d' % p['version'])
        for i in range(HEALTH_WAIT):
            prog.update(85 + 10.0 * i / HEALTH_WAIT, 'Step 3 of 3 - checking that everything works…')
            if monitor.waitForAbort(1):
                prog.close()
                return
        prog.close()
        why = broken_after_update(p)
        if why:
            rollback(p, st, why)
            return
    if p.get('enable'):
        enable_addons(p['enable'])
    st['version'] = p['version']
    st['applied'] = p.get('once', {})
    st['pending'] = None
    st['finish'] = {'version': p['version'], 'from': p.get('from', '?'), 'files': len(p['files']),
                    'actions': list(p.get('actions', [])), 'results': {}, 'running': None, 'started': 0}
    save_state(st)
    prune_backups()
    finish(monitor)


# ---------------------------------------------------------------- one-time actions + final screen

def live_tv_ready(monitor, prog, limit=150):
    for i in range(limit):
        r = rpc('PVR.GetChannels', channelgroupid='alltv', limits={'start': 0, 'end': 1})
        if r.get('result', {}).get('limits', {}).get('total', 0) > 0:
            monitor.waitForAbort(10)  # let the channel list settle in the database
            return True
        prog.update(96, 'Waiting for Live TV to load…')
        if monitor.waitForAbort(1):
            return False
    return False


def rc_of(aid):
    try:
        return open(os.path.join(ACTIONS, aid + '.rc')).read().strip()
    except (IOError, OSError):
        return None


def finish(monitor):
    """Run the one-time actions of this update (they may restart Kodi; resumes here), then report."""
    st = load_state()
    f = st.get('finish')
    if not f:
        return
    prog = Progress('Box update v%d' % f['version'])
    while f.get('running'):
        aid = f['running']
        rc = rc_of(aid)
        if rc is None and time.time() - f['started'] < ACTION_TIMEOUT:
            prog.update(97, 'Finishing: %s…' % aid)
            if monitor.waitForAbort(2):
                prog.close()
                return
            continue
        f['results'][aid] = 'ok' if rc == '0' else ('error %s' % rc if rc else 'timeout')
        f['running'] = None
        save_state(st)
    while f['actions']:
        aid = f['actions'][0]
        script = os.path.join(ACTIONS, aid + '.sh')
        if not live_tv_ready(monitor, prog):
            log('Live TV not loaded - running %s anyway' % aid, xbmc.LOGWARNING)
        if monitor.abortRequested():
            prog.close()
            return
        prog.update(97, 'Finishing: %s…' % aid)
        try:
            os.remove(os.path.join(ACTIONS, aid + '.rc'))
        except OSError:
            pass
        f['actions'].pop(0)
        f['running'], f['started'] = aid, time.time()
        st.setdefault('done', []).append(aid)  # recorded first: never run twice, even if it kills Kodi
        save_state(st)
        log('running action %s' % aid)
        rcfile = os.path.join(ACTIONS, aid + '.rc')
        subprocess.Popen(['systemd-run', '--unit=smoke-action-%s-%d' % (re.sub(r'[^A-Za-z0-9-]', '', aid), int(time.time())),
                          '/bin/sh', '-c', '/bin/sh "%s"; echo $? > "%s"' % (script, rcfile)])
        while True:  # if the action restarts Kodi we die here and pick it up again after the restart
            rc = rc_of(aid)
            if rc is not None or time.time() - f['started'] > ACTION_TIMEOUT:
                break
            if monitor.waitForAbort(2):
                prog.close()
                return
        f['results'][aid] = 'ok' if rc == '0' else ('error %s' % rc if rc else 'timeout')
        f['running'] = None
        save_state(st)
    prog.update(100, 'Done')
    xbmc.sleep(800)
    prog.close()
    st['finish'] = None
    save_state(st)
    bad = {k: v for k, v in f['results'].items() if v != 'ok'}
    lines = ['%s v%s -> v%d  (%d files)' % ('⚠️' if bad else '✅', f['from'], f['version'], f['files'])]
    lines += ['%s %s: %s' % ('✓' if v == 'ok' else '✗', k, v) for k, v in sorted(f['results'].items())]
    report('\n'.join(lines))
    status('uptodate', 'Up to date · v%d' % f['version'], f['version'])
    log('version %d installed: %s' % (f['version'], f['results']))
    msg = 'Your box is up to date - version %d.' % f['version']
    if bad:
        msg += '[CR]Note: %s did not finish.' % ', '.join(sorted(bad))
    xbmcgui.Dialog().ok('Box update complete', msg)


# ---------------------------------------------------------------- idle install with countdown

def countdown(monitor, ver):
    d = xbmcgui.DialogProgress()
    d.create('Box update v%d' % ver, '')
    for i in range(COUNTDOWN, 0, -1):
        d.update(int(100.0 * (COUNTDOWN - i) / COUNTDOWN),
                 'A new version is ready. Installing in %d s (Kodi restarts, about 1 minute).[CR]Press Back to do it later.' % i)
        for _ in range(10):
            if d.iscanceled() or monitor.waitForAbort(0.1):
                d.close()
                return False
    d.close()
    return True


def main():
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    FIRST['on'] = not os.path.exists(STATE)
    st = load_state()
    status('installing' if (st.get('pending') or st.get('finish')) else 'uptodate', 'Box version v%d' % st.get('version', 0))
    verify_pending(monitor)
    finish(monitor)
    if monitor.abortRequested() or load_state().get('pending'):
        return
    next_check = time.time() + BOOT_CHECK
    boot_tries = BOOT_RETRIES
    postponed = 0
    while not monitor.waitForAbort(3):
        asked = HOME.getProperty('SmokeUpdate.Request')
        if asked:
            HOME.clearProperty('SmokeUpdate.Request')
        if asked or time.time() >= next_check:
            boot = boot_tries > 0
            fg = bool(asked) or (boot and not player.isPlaying())
            try:
                if asked:
                    status('checking', 'Checking…')
                res = check(monitor, fg)
                boot_tries = 0
                if res == 'ready' and (fg or asked == 'install'):
                    install()
                    if load_state().get('pending'):
                        return  # Kodi is restarting
                elif asked and res == 'uptodate':
                    notify('Up to date (v%d)' % load_state().get('version', 0))
                next_check = time.time() + CHECK_EVERY
            except Exception as e:
                log('check failed: %s' % e, xbmc.LOGWARNING)
                if boot_tries > 0:
                    boot_tries -= 1
                    next_check = time.time() + 15
                else:
                    next_check = time.time() + CHECK_EVERY
                if asked:
                    status('failed', 'Update check failed · v%d' % load_state().get('version', 0))
                    notify('Could not check for updates - is the box online?', xbmcgui.NOTIFICATION_WARNING)
            continue
        if READY and time.time() >= postponed and not player.isPlaying() and xbmc.getGlobalIdleTime() >= IDLE_NEEDED:
            if countdown(monitor, READY['ver']):
                install()
                if load_state().get('pending'):
                    return
            else:
                postponed = time.time() + POSTPONE


if __name__ == '__main__':
    main()
