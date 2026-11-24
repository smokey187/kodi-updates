#!/bin/sh
# One-time: number Live TV channels in a row - Israel 1-33 (the box's 'ראשי' order), then Poland,
# News, Sports, Movies, Kids, Music, Nature & Relax, Travel & Fashion (plugin.video.tvgroups/resources/groups.json),
# the rest after them - and stop Kodi from re-sorting them by the backend order.
LOG=/storage/.kodi/temp/smoke-action-renumber-1.log
exec >"$LOG" 2>&1
set -x
G=/storage/.kodi/addons/plugin.video.tvgroups/resources/groups.json
[ -f "$G" ] || { echo "no groups.json yet"; exit 0; }
systemctl stop kodi
sleep 3
S=/storage/.kodi/userdata/guisettings.xml
sed -i 's#<setting id="pvrmanager.backendchannelorder"[^>]*>true</setting>#<setting id="pvrmanager.backendchannelorder">false</setting>#' "$S"
grep -q 'pvrmanager.backendchannelorder' "$S" || sed -i 's#</settings>#    <setting id="pvrmanager.backendchannelorder">false</setting>\n</settings>#' "$S"
python3 - "$G" <<'PY'
import sqlite3, json, glob, sys
g = json.load(open(sys.argv[1], encoding='utf-8'))
db = sorted(glob.glob('/storage/.kodi/userdata/Database/TV*.db'))[-1]
c = sqlite3.connect(db)
allg = c.execute("select idGroup from channelgroups where sName='All channels' and bIsRadio=0").fetchone()[0]
members = c.execute("select m.idChannel, ch.sChannelName from map_channelgroups_channels m join channels ch on ch.idChannel=m.idChannel where m.idGroup=? order by m.iChannelNumber", (allg,)).fetchall()
byname = {}
for cid, n in members:
    byname.setdefault(n, cid)
order = [r[0] for r in c.execute("select m.idChannel from map_channelgroups_channels m join channelgroups gr on gr.idGroup=m.idGroup where gr.sName='ראשי' and gr.bIsRadio=0 order by m.iChannelNumber")]
for k in ['פולין', 'חדשות', 'ספורט', 'סרטים', 'ילדים', 'מוזיקה', 'טבע ורגיעה', 'טיולים ואופנה']:
    for n in g.get(k, []):
        cid = byname.get(n)
        if cid and cid not in order:
            order.append(cid)
order += [cid for cid, _ in members if cid not in order]
for i, cid in enumerate(order, 1):
    c.execute("update map_channelgroups_channels set iChannelNumber=?, iOrder=?, iSubChannelNumber=0 where idGroup=? and idChannel=?", (i, i, allg, cid))
c.commit()
print('numbered', len(order), 'in', db)
PY
systemctl start kodi
echo done
