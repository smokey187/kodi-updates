# -*- coding: utf-8 -*-
# NF boot priority (claude 20261115): let Elementum start first so the home rows fill quickly
def _nf_wait_for_elementum(limit=45):
    import socket, time, xbmc
    mon, t0 = xbmc.Monitor(), time.time()
    while time.time() - t0 < limit and not mon.abortRequested():
        try:
            socket.create_connection(('127.0.0.1', 65220), 1).close()
            return
        except Exception:
            mon.waitForAbort(1)
_nf_wait_for_elementum()
# /NF boot priority

#
import logging
import xbmcaddon
from resources.lib import kodilogging
from resources.lib.service import traktService
from resources.lib.utilities import createError, checkIfNewVersion
from resources.lib.kodiUtilities import setSetting, getSetting

__addon__ = xbmcaddon.Addon('script.trakt')
__addonversion__ = __addon__.getAddonInfo('version')
__addonid__ = __addon__.getAddonInfo('id')
kodilogging.config()
logger = logging.getLogger(__name__)

logger.debug("Loading '%s' version '%s'" % (__addonid__, __addonversion__))
if checkIfNewVersion(str(getSetting('version')), str(__addonversion__)):
    setSetting('version', __addonversion__)

try:
    traktService().run()
except Exception as ex:
    message = createError(ex)
    logger.fatal(message)

logger.debug("'%s' shutting down." % __addonid__)
