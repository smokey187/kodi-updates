#!/bin/sh
# One-time cleanup on boxes that were cloned from smoke's master box:
# log his Trakt account out, clear the watched / resume markers and search history
# copied with the clone. Run by service.smoke.updater through systemd-run, so it
# survives stopping Kodi. No backups on purpose: they would keep his tokens around.
U=/storage/.kodi/userdata
A=$U/addon_data
LOG=/storage/.kodi/temp/smoke-action-depersonalize-1.log
exec >"$LOG" 2>&1
set -x

systemctl stop kodi
sleep 3

blank() {  # blank <file> <setting id>...
    f=$1; shift
    [ -f "$f" ] || return 0
    for id in "$@"; do
        sed -i -E "s#(<setting id=\"$id\"[^>]*)>[^<]*</setting>#\1></setting>#" "$f"
    done
}
blank "$A/script.trakt/settings.xml" user authorization
blank "$A/plugin.video.elementum/settings.xml" trakt_username trakt_token trakt_refresh_token trakt_token_expiry
blank "$A/skin.arctic.fuse/settings.xml" searchhistory

for DB in $U/Database/MyVideos*.db; do
    sqlite3 "$DB" "UPDATE files SET playCount=NULL, lastPlayed=NULL; DELETE FROM bookmark;"
done

# Elementum and TMDb Helper caches hold the synced Trakt history / searches; both rebuild themselves
rm -f "$A/plugin.video.elementum/storm.db" "$A/plugin.video.elementum/storm-backup.db" \
      "$A/plugin.video.elementum/cache.db" "$A/plugin.video.elementum/cache-backup.db"
rm -rf "$A/plugin.video.themoviedb.helper/database_07"

systemctl start kodi
echo done
