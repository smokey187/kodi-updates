# kodi-updates

Automatic updates for smoke's CoreELEC boxes (X96 Plus Max, CoreELEC 21.3).

- `payload/` – the customized files, laid out like `/storage/.kodi` on the box.
- `manifest.json` – version + sha256 of every file + the add-on versions the patches were made for.
- `updater/service.smoke.updater/` – the Kodi service that runs on every box.
- `docs/` – GitHub Pages: the one-time install zip.
- `tools/publish.py` – rebuilds the manifest and zip (bumps the version only when something changed).

## What a box does

Every ~3 h: fetch the manifest → find files that differ (new version, or an add-on update
overwrote a patch) → download + verify sha256 → wait until nothing is playing and the remote
was idle 10 min → back up → swap files → restart Kodi → 60 s later check that the skin loaded
and no updated Python file raised an error → keep it, or roll back and never retry that version.

Patches for a third-party add-on are skipped if the box has a different version of that add-on
than `tools/config.json` says (so an Elementum/skin update never gets mixed with old patches).

## One-time install on a box

1. Settings → System → Add-ons → **Unknown sources: on**
2. Settings → File manager → Add source → `https://smokey187.github.io/kodi-updates/` → name `smoke`
3. Add-ons → Install from zip file → `smoke` → `service.smoke.updater.zip`

## Publishing a change

**From the master box (.143):** Programs → *Publish box update* → check the list → Yes.
It pushes the changed files + a new manifest straight to GitHub (token in its add-on settings,
fine-grained, Contents read/write on this repo only). Changes to the updater itself go through the manual way.

**Manually** (run `git pull` first - the box commits too):

1. Copy the changed files from the master box (.143) into `payload/` (same paths).
2. If an add-on was upgraded on the master box, update its version in `tools/config.json`.
3. `python3 tools/publish.py`, then commit and push.
