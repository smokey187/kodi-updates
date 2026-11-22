# "Box update" button: shows the update status and lets the user install now.
import xbmcgui

home = xbmcgui.Window(10000)
state = home.getProperty('SmokeUpdate.State')
local = home.getProperty('SmokeUpdate.Local') or '?'
remote = home.getProperty('SmokeUpdate.Remote') or '?'
dlg = xbmcgui.Dialog()

if state == 'installing':
    dlg.ok('Box update', 'Installing version %s - Kodi will restart in a moment.' % remote)
elif state == 'ready':
    if dlg.yesno('Box update', 'Version %s is ready (this box has %s).[CR]Install now? Kodi will restart.' % (remote, local),
                 nolabel='Later', yeslabel='Install now'):
        home.setProperty('SmokeUpdate.Request', 'install')
        dlg.notification('Box update', 'Installing version %s…' % remote, xbmcgui.NOTIFICATION_INFO, 4000)
else:
    msg = 'This box has version %s.' % local
    if state == 'failed':
        msg += '[CR]The last update attempt failed - the box went back to the working version.'
    if dlg.yesno('Box update', msg + '[CR]Check for updates now?', nolabel='Close', yeslabel='Check now'):
        home.setProperty('SmokeUpdate.Request', 'check')
        dlg.notification('Box update', 'Checking for updates…', xbmcgui.NOTIFICATION_INFO, 3000)
