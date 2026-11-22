#!/usr/bin/env python3
"""Build manifest.json + the install zip from payload/. Run from the repo root, then commit + push.

payload/ mirrors /storage/.kodi on the box (addons/..., userdata/...).
The version only goes up when some file actually changed.
"""
import hashlib
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(ROOT, 'payload')
UPDATER = os.path.join(ROOT, 'updater', 'service.smoke.updater')
DOCS = os.path.join(ROOT, 'docs')
CONF = json.load(open(os.path.join(ROOT, 'tools', 'config.json')))


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def main():
    # the updater ships inside the payload too, so it updates itself
    dst = os.path.join(PAYLOAD, 'addons', 'service.smoke.updater')
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(UPDATER, dst)

    files, bad = {}, []
    for base, dirs, names in os.walk(PAYLOAD):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for n in sorted(names):
            p = os.path.join(base, n)
            rel = os.path.relpath(p, PAYLOAD).replace(os.sep, '/')
            if n.endswith('.pyc') or '.bak' in n:
                continue
            if n.endswith('.py'):
                try:
                    compile(open(p, 'rb').read(), rel, 'exec')
                except SyntaxError as e:
                    bad.append('%s: %s' % (rel, e))
            files[rel] = {'sha256': sha(p), 'size': os.path.getsize(p)}
            if rel.startswith('userdata/') or 'skinvariables' in n or 'skinviewtypes' in n:
                files[rel]['once'] = True  # Kodi/scripts rewrite these on the box: apply on repo change only
    if bad:
        sys.exit('refusing to publish, syntax errors:\n' + '\n'.join(bad))

    mpath = os.path.join(ROOT, 'manifest.json')
    old = json.load(open(mpath)) if os.path.exists(mpath) else {'version': 0, 'files': {}}
    changed = old.get('files') != files or old.get('requires') != CONF['requires'] or old.get('enable') != CONF['enable']
    version = old['version'] + 1 if changed else old['version']
    actions = []
    for a in CONF.get('actions', []):
        actions.append({'id': a['id'], 'script': a['script'], 'sha256': sha(os.path.join(ROOT, a['script']))})
    changed = changed or old.get('actions', []) != actions
    version = old['version'] + 1 if changed else old['version']
    man = {'version': version, 'requires': CONF['requires'], 'enable': CONF['enable'], 'actions': actions, 'files': files}
    with open(mpath, 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)

    # one-time install zip for new boxes (served by GitHub Pages from docs/)
    os.makedirs(DOCS, exist_ok=True)
    zpath = os.path.join(DOCS, 'service.smoke.updater.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _, names in os.walk(UPDATER):
            for n in names:
                if n.endswith('.pyc'):
                    continue
                p = os.path.join(base, n)
                z.write(p, os.path.join('service.smoke.updater', os.path.relpath(p, UPDATER)))
    with open(os.path.join(DOCS, 'index.html'), 'w') as f:
        f.write('<html><body><a href="service.smoke.updater.zip">service.smoke.updater.zip</a></body></html>\n')

    print('version %d%s, %d files' % (version, '' if changed else ' (unchanged)', len(files)))


if __name__ == '__main__':
    main()
