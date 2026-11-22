# Publish box update - runs on the master box (.143) only.
#
# 1. read the live manifest from GitHub
# 2. find changed files: every tracked file whose sha256 differs, plus newly edited files in the
#    tracked add-ons (mtime outside the add-on's install time) and new files in our own add-ons
# 3. show the list, check Python syntax + XML well-formedness
# 4. push one commit (payload files + manifest with version+1) through the GitHub API
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

import xbmc
import xbmcaddon
import xbmcgui

OWNER, REPO, BRANCH = 'smokey187', 'kodi-updates', 'main'
API = 'https://api.github.com/repos/%s/%s' % (OWNER, REPO)
KODI = '/storage/.kodi'
UPDATER = 'addons/service.smoke.updater/'
SKIP = re.compile(r'(\.bak|\.orig|\.old|\.tmp|\.pyc|\.swp|~$|\.before_|crashtest|\.nf-panel|/__pycache__/)')
TITLE = 'Publish box update'

ADDON = xbmcaddon.Addon()


def token():
    t = ADDON.getSetting('token').strip()
    if not t:
        p = os.path.join(KODI, 'userdata', 'addon_data', 'script.smoke.publish', 'token.txt')
        if os.path.exists(p):
            t = open(p).read().strip()
    return t


def gh(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + token(), 'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'smoke-publish', 'Content-Type': 'application/json'})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError('GitHub %s %s: %s %s' % (method, path, e.code, e.read().decode()[:200]))


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()


def addon_version(aid):
    try:
        m = re.search(r'<addon\b[^>]*\bversion="([^"]+)"', open(os.path.join(KODI, 'addons', aid, 'addon.xml'), errors='ignore').read())
        return m.group(1) if m else None
    except (IOError, OSError):
        return None


def walk(aid):
    base = os.path.join(KODI, 'addons', aid)
    for d, dirs, names in os.walk(base):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, KODI)
            if not SKIP.search(rel):
                yield rel, p


def edited_in(aid):
    """Files of a third-party add-on edited after it was installed (install = the dominant mtime hour)."""
    files = [(rel, time.strftime('%Y%m%d%H', time.localtime(os.path.getmtime(p)))) for rel, p in walk(aid)]
    if not files:
        return []
    install = Counter(h for _, h in files).most_common(1)[0][0]
    return [rel for rel, h in files if h != install]


def once(rel):
    n = os.path.basename(rel)
    return rel.startswith('userdata/') or 'skinvariables' in n or 'skinviewtypes' in n


def check_file(rel):
    p = os.path.join(KODI, rel)
    try:
        if rel.endswith('.py'):
            compile(open(p, 'rb').read(), rel, 'exec')
        elif rel.endswith('.xml'):
            ET.parse(p)
    except Exception as e:
        return '%s: %s' % (rel, e)
    return None


def main():
    dlg = xbmcgui.Dialog()
    if not token():
        dlg.ok(TITLE, 'No GitHub token yet.[CR]Add-on settings -> GitHub token.')
        ADDON.openSettings()
        return

    prog = xbmcgui.DialogProgress()
    prog.create(TITLE, 'Reading the current version from GitHub…')
    ref = gh('GET', '/git/ref/heads/' + BRANCH)
    head = ref['object']['sha']
    man = json.loads(base64.b64decode(gh('GET', '/contents/manifest.json?ref=' + head)['content']))
    files = man['files']
    req = dict(man.get('requires', {}))

    prog.update(20, 'Looking for changes…')
    candidates = set(p for p in files if not p.startswith(UPDATER))
    for aid in req:
        candidates.update(edited_in(aid))
    for aid in man.get('enable', []):
        if aid != 'service.smoke.updater':
            candidates.update(rel for rel, _ in walk(aid))

    changed, missing = [], []
    for rel in sorted(candidates):
        p = os.path.join(KODI, rel)
        if not os.path.isfile(p):
            if rel in files:
                missing.append(rel)
            continue
        if rel not in files or files[rel]['sha256'] != sha256(p):
            changed.append(rel)

    bumped = []
    for aid, v in req.items():
        have = addon_version(aid)
        if have and have != v:
            bumped.append('%s %s -> %s' % (aid, v, have))
            req[aid] = have
    prog.close()

    if not changed and not bumped:
        dlg.ok(TITLE, 'Nothing changed since version %d.' % man['version'])
        return

    errors = [e for e in (check_file(r) for r in changed) if e]
    if errors:
        dlg.textviewer(TITLE + ' - NOT published', 'Fix these first:\n\n' + '\n'.join(errors))
        return

    lines = ['%s %s' % ('NEW' if r not in files else '   ', r) for r in changed]
    if bumped:
        lines = ['Add-on version now required on the boxes:'] + ['  ' + b for b in bumped] + [''] + lines
    if missing:
        lines += ['', 'Gone from this box (kept on the other boxes):'] + ['  ' + m for m in missing]
    dlg.textviewer('%s - version %d -> %d (%d files)' % (TITLE, man['version'], man['version'] + 1, len(changed)), '\n'.join(lines))
    if not dlg.yesno(TITLE, 'Publish version %d to all boxes?' % (man['version'] + 1)):
        return

    prog = xbmcgui.DialogProgress()
    prog.create(TITLE, 'Uploading…')
    tree = []
    for i, rel in enumerate(changed):
        if prog.iscanceled():
            return
        prog.update(int(80 * i / max(1, len(changed))), rel)
        p = os.path.join(KODI, rel)
        blob = gh('POST', '/git/blobs', {'content': base64.b64encode(open(p, 'rb').read()).decode(), 'encoding': 'base64'})
        tree.append({'path': 'payload/' + rel, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        files[rel] = {'sha256': sha256(p), 'size': os.path.getsize(p)}
        if once(rel):
            files[rel]['once'] = True

    man['version'] += 1
    man['requires'] = req
    man['files'] = files
    prog.update(85, 'Writing version %d…' % man['version'])
    mblob = gh('POST', '/git/blobs', {'content': json.dumps(man, indent=1, sort_keys=True), 'encoding': 'utf-8'})
    tree.append({'path': 'manifest.json', 'mode': '100644', 'type': 'blob', 'sha': mblob['sha']})
    base_tree = gh('GET', '/git/commits/' + head)['tree']['sha']
    new_tree = gh('POST', '/git/trees', {'base_tree': base_tree, 'tree': tree})
    commit = gh('POST', '/git/commits', {'message': 'Publish version %d from %s (%d files)' % (man['version'], xbmc.getInfoLabel('System.FriendlyName') or 'master box', len(changed)),
                                         'tree': new_tree['sha'], 'parents': [head]})
    gh('PATCH', '/git/refs/heads/' + BRANCH, {'sha': commit['sha']})  # fails if someone pushed meanwhile (no force)
    prog.close()
    dlg.ok(TITLE, 'Published version %d.[CR]The boxes will install it within a few hours, when they are idle.' % man['version'])


try:
    main()
except Exception as e:
    xbmc.log('[smoke.publish] %s' % e, xbmc.LOGERROR)
    xbmcgui.Dialog().ok(TITLE, 'Failed - nothing was published.[CR]%s' % str(e)[:300])
