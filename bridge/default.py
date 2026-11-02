"""script.silo is now an update helper: Founders Theater moved to script.founderstheater.

Kodi cannot rename an add-on. This last script.silo release requires the new
add-on, so Kodi installs it with the update, and passes RunScript calls such as
Kodi favourites and older widget links on to it unchanged. The new add-on
copies this device's sign-in and settings over on its first run.
"""

import sys

import xbmc

NEW_ID = "script.founderstheater"


def forward(argv):
    """The builtin that opens the new add-on with the same arguments."""
    return "RunScript({})".format(",".join([NEW_ID] + [str(arg) for arg in argv[1:]]))


if __name__ == "__main__":
    xbmc.executebuiltin(forward(sys.argv))
