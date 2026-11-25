# -*- coding: utf-8 -*-
"""

    Copyright (C) 2014-2016 bromix (plugin.video.youtube)
    Copyright (C) 2016-2025 plugin.video.youtube

    SPDX-License-Identifier: GPL-2.0-only
    See LICENSES/GPL-2.0-only for more information.
"""

from __future__ import absolute_import, division, unicode_literals
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


from youtube_plugin.kodion import service_runner


service_runner.run()
