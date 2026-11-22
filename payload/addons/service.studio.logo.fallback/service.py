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

# StudioLogoFallback - claude 20261114 rewrite:
#  * tries EVERY studio of "A / B / C" (was: only the first)
#  * case-insensitive name index read once from Textures.xbt (was: up to 3 xbmcvfs.exists calls every 0.2s)
#  * recomputes only when the studio string changes
import os, struct
import xbmc
import xbmcgui
import xbmcvfs

WINDOW = xbmcgui.Window(10000)
ADDON_ID = "resource.images.studios.coloured"
RESOURCE = "resource://%s/" % ADDON_ID
XBT = xbmcvfs.translatePath("special://home/addons/%s/resources/Textures.xbt" % ADDON_ID)
# Hand-made logos for studios the package lacks (survives package updates). File name = studio name + .png
CUSTOM_DIR = xbmcvfs.translatePath("special://profile/addon_data/service.studio.logo.fallback/logos/")

ALIASES = {
    "apple tv": "Apple TV+",
    "illumination": "Illumination Entertainment",
    "20th century studios": "20th Century Fox Studios",
    "dc studios": "DC",
    "prime video": "Amazon Prime Video",
    "mgm+": "MGM",
    "stxfilms": "STX Entertainment",
    "nippon tv": "NTV",
}

# Names known to have no logo in the package: skip them (and no first-word guess for them).
NO_LOGO = {"lord miller", "tea shop productions", "t-street", "raimi productions"}


def load_index():
    """Lower-case names (without .png) of every logo packed in Textures.xbt; None if it can't be read."""
    try:
        names = set()
        with open(XBT, "rb") as f:
            if f.read(4) != b"XBTF":
                return None
            f.read(1)
            count = struct.unpack("<I", f.read(4))[0]
            for _ in range(count):
                path = f.read(256).split(b"\0")[0].decode("utf-8", "replace")
                _loop, frames = struct.unpack("<II", f.read(8))
                f.seek(frames * 40, os.SEEK_CUR)
                if path.lower().endswith(".png"):
                    names.add(path[:-4].lower())
        return names
    except Exception as error:
        xbmc.log("[StudioLogoFallback] index read failed: %s" % error, xbmc.LOGWARNING)
        return None


INDEX = load_index()


def load_custom():
    try:
        return {f[:-4].lower(): os.path.join(CUSTOM_DIR, f) for f in os.listdir(CUSTOM_DIR) if f.lower().endswith(".png")}
    except Exception:
        return {}


CUSTOM = load_custom()


def has_logo(name):
    if INDEX is not None:
        return name.lower() in INDEX
    return xbmcvfs.exists(RESOURCE + name + ".png")


def match(name):
    key = name.lower()
    if key in ALIASES and has_logo(ALIASES[key]):
        return ALIASES[key]
    if key in NO_LOGO:
        return None
    if has_logo(name):
        return name
    return None


def find_logo(source):
    if not source:
        return ""
    studios = [s.strip() for s in source.split(" / ") if s.strip()]
    for studio in studios:
        if studio.lower() in CUSTOM:
            return CUSTOM[studio.lower()]
        name = match(studio)
        if name:
            return RESOURCE + name + ".png"
        if studio.lower() in NO_LOGO:
            continue
        first = studio.split()[0]
        if first != studio and len(first) >= 3 and has_logo(first):
            return RESOURCE + first + ".png"
    return ""


def get_studio():
    for label in ("ListItem.Studio",
                  "Window(Home).Property(TMDbHelper.ListItem.base_studio)",
                  "Window(Home).Property(TMDbHelper.ListItem.Studio.1.Name)"):
        value = xbmc.getInfoLabel(label)
        if value:
            return value
    return ""


monitor = xbmc.Monitor()
xbmc.log("[StudioLogoFallback] STARTED (index: %s logos, custom: %d)" % (len(INDEX) if INDEX is not None else "vfs", len(CUSTOM)), xbmc.LOGINFO)

last_source = None
while not monitor.abortRequested():
    try:
        source = get_studio()
        if source != last_source:
            last_source = source
            logo = find_logo(source)
            if logo:
                WINDOW.setProperty("StudioLogoPath", logo)
                xbmc.log("[StudioLogoFallback] LOGO: %s -> %s" % (source, logo), xbmc.LOGINFO)
            else:
                WINDOW.clearProperty("StudioLogoPath")
                if source:
                    xbmc.log("[StudioLogoFallback] NO LOGO: %s" % source, xbmc.LOGINFO)
    except Exception as error:
        xbmc.log("[StudioLogoFallback] ERROR: %s" % error, xbmc.LOGERROR)
    if monitor.waitForAbort(0.2):
        break
