# -*- coding: utf-8 -*-
# Full-screen grid of square channel cards (8 per row) for one Live TV group. Usage: RunScript(<this file>, <group name>, <title>)
import sys, os, json
import xbmc, xbmcgui
PATH = os.path.dirname(os.path.abspath(__file__))

def rpc(method, **p):
    return json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': p}))).get('result', {})

class Grid(xbmcgui.WindowXML):
    def __init__(self, *a, **k):
        self.group, self.title, self.play = k.get('group'), k.get('title'), None
    def onInit(self):
        self.getControl(10).setLabel(self.title)
        groups = rpc('PVR.GetChannelGroups', channeltype='tv').get('channelgroups', [])
        names = json.load(open(os.path.join(PATH, 'resources', 'groups.json'), encoding='utf-8')).get(self.group)
        if names:  # groups are defined by channel name in groups.json, so they work on every box
            gid = next((g['channelgroupid'] for g in groups if g.get('label') == 'All channels'), None) or 'alltv'
            allc = rpc('PVR.GetChannels', channelgroupid=gid, properties=['thumbnail']).get('channels', [])
            by = {}
            for c in allc:
                by.setdefault(c['label'], c)
            chans = [by[n] for n in names if n in by]
        else:  # Israel = the box's own 'ראשי' group
            gid = next((g['channelgroupid'] for g in groups if g['label'] == self.group), None)
            chans = rpc('PVR.GetChannels', channelgroupid=gid, properties=['thumbnail']).get('channels', []) if gid is not None else []
        items = []
        for ch in chans:
            li = xbmcgui.ListItem(ch['label'])
            li.setArt({'icon': ch.get('thumbnail') or ''})
            li.setProperty('channelid', str(ch['channelid']))
            items.append(li)
        panel = self.getControl(100)
        panel.reset(); panel.addItems(items)
        self.setFocusId(100)
    def onClick(self, cid):
        if cid == 100:
            self.play = int(self.getControl(100).getSelectedItem().getProperty('channelid'))
            self.close()

if __name__ == '__main__':
    g = sys.argv[1] if len(sys.argv) > 1 else 'ראשי'
    t = sys.argv[2] if len(sys.argv) > 2 else g
    w = Grid('tvgrid.xml', PATH, 'Default', '1080i', group=g, title=t)
    w.doModal()
    play = w.play
    del w
    if play:
        rpc('Player.Open', item={'channelid': play})
