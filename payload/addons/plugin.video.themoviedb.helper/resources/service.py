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

from tmdbhelper.lib.monitor.service import ServiceMonitor
from tmdbhelper.lib.items.database.details.client import DetailsClient


if __name__ == '__main__':
    DetailsClient().start()
    ServiceMonitor().run()
