# Linux acceptance: the tray icon, in one sitting

TASK-112.11 criteria 3 and 4, ADR-038. **Not run.** Every point needs a real
Linux desktop; the tray was wired and unit-tested on Windows, with fakes for
the X11 tray. The AppImage carries pystray's xorg backend only, so the icon
needs an X11 system tray (XEmbed): KDE Plasma, XFCE, Cinnamon and MATE have
one; GNOME has one only with an extension that shows tray icons, such as
"AppIndicator and KStatusNotifierItem Support". Point 3 checks the fallback.

Use a release AppImage that has TASK-112.11, with Start at login switched on
in Settings, and do the points in one session. Paste each command with its
output, and say "ok" or what was different.

    L=~/.local/share/MyScribe/logs/launcher.log    # or <your home>/logs

## 1. A desktop with a system tray (KDE Plasma or XFCE)

1. Log out and in. Expected: an icon in the tray, and
   `grep -i tray "$L"` says "MyScribe shows a tray icon". `echo $XDG_SESSION_TYPE`
   tells us X11 or Wayland: paste it.
2. Click the icon, then right-click it. Expected: a menu with **Open MyScribe**
   and **Stop MyScribe**. Take a screenshot. **Open MyScribe** opens
   `http://127.0.0.1:4242/`.
3. Queue a transcription, and while it runs choose **Stop MyScribe**.
   Expected: the icon goes, `curl -s http://127.0.0.1:4242/health` answers
   nothing, `systemctl --user status myscribe` (if the unit is in use) shows
   it inactive and not restarting, and the next start has the job back at the
   front of the queue.

## 2. Stops from outside

Start it again (`systemctl --user start myscribe`, or log out and in).

1. `./MyScribe-<version>-linux-x64.AppImage --stop`. Expected: exit 0 and the
   icon goes.
2. Start it again, then `pkill -TERM -f MyScribe-.*AppImage`. Expected: the
   icon goes and `pgrep -fl scribe` shows nothing left.
3. Start it again, then `kill -9` the app's python (`pgrep -fl "scribe --no-browser"`).
   Expected: the icon goes, and systemd starts MyScribe again with a new icon.

## 3. GNOME without a tray extension (Ubuntu's default on Wayland, extension off)

1. Log out and in. Expected: no icon, MyScribe serves on `/health`, and
   `grep -i tray "$L"` says "No tray icon (nothing on this desktop shows tray
   icons (no X11 system tray))". Paste that line.
2. Switch the AppIndicator extension on, log out and in, and say whether an
   icon appears. (Unknown: the extension hosts legacy XEmbed icons on X11;
   whether it does under Wayland through XWayland has not been checked.)
