#!/usr/bin/env python3
"""paletti: switch the GNOME desktop color theme with one command.

    paletti              switch to the next theme
    paletti everforest   switch to a specific theme
    paletti --list       list themes
    paletti --install    ~/.local/bin/paletti symlink + Super+Y / Super+Shift+Y shortcuts

    paletti --next-wallpaper            next wallpaper of the current palette
    paletti --wallpaper default|chosen|keep
        default: the palette's wallpaper, chosen: the last one picked with
        --next-wallpaper, keep: never touch the wallpaper.
        Own wallpapers go under [wallpapers] in ~/.config/paletti/config.toml.

    sudo paletti --gdm-setup    the GDM login screen follows the palette from now on;
                                run again after changing palettes or their wallpapers
    sudo paletti --gdm-remove   GDM back to GNOME's own theme
"""

import ast
import colorsys
import configparser
import fcntl
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
PALETTES = HERE / "palettes"
HOME = Path.home()
EXTENSIONS = HOME / ".local/share/gnome-shell/extensions"
THEME_CSS = HOME / ".local/share/themes/Islands/gnome-shell/gnome-shell.css"
STATE = HOME / ".local/state/paletti/current"
LOCK = HOME / ".local/state/paletti/lock"
ICONS_PID = HOME / ".local/state/paletti/icons.pid"
ICON_THEME_SWAP = HOME / ".local/state/paletti/icon-theme"  # exists only mid set_icon_theme
CONFIG = HOME / ".config/paletti/config.toml"
CHOSEN_WALLPAPER = HOME / ".local/state/paletti/wallpaper"  # one file per palette
WALLPAPER_MODES = ("default", "chosen", "keep")

# GDM: GNOME's theme resource gets gdm.css appended, by a root service that only
# uses a root-owned copy of paletti, so the user's own files are never run as root.
GDM_GRESOURCE = Path("/usr/share/gnome-shell/gnome-shell-theme.gresource")
GDM_ORIGINAL = Path("/var/lib/paletti/gnome-shell-theme.gresource")  # as GNOME shipped it
GDM_HOME = Path("/usr/local/share/paletti")
GDM_UNITS = Path("/etc/systemd/system")
GDM_HOOK = Path("/etc/pacman.d/hooks/paletti-gdm.hook")
GDM_MARKER = b"paletti-gdm"

GTK_CSS = {"gtk4.css": HOME / ".config/gtk-4.0/gtk.css",
           "gtk3.css": HOME / ".config/gtk-3.0/gtk.css"}
FIREFOX_DIRS = [HOME / ".config/mozilla/firefox", HOME / ".mozilla/firefox"]  # newer and older location
VSCODE_DIRS = [HOME / ".config/Code/User", HOME / ".config/VSCodium/User", HOME / ".config/Code - OSS/User"]

ROLES = ["bg", "bg_alt", "fg", "surface", "surface_hover", "card", "card_alt", "card_hover",
         "accent", "accent_hover", "accent2", "heading", "muted"]

ICON_DIRS = [HOME / ".local/share/icons", HOME / ".icons", Path("/usr/share/icons")]
ICON_COLORS = ["black", "blue", "brown", "cyan", "green", "grey", "indigo", "magenta", "nordic",
               "orange", "pink", "purple", "red", "teal", "violet", "white", "yellow"]

# Tela draws every colored part with one highlight color, so one installed
# Tela package can be recolored to any palette accent.
TELA_SRC = Path("/usr/share/icons")
TELA_BLUE = re.compile(rb"#5294e2", re.IGNORECASE)
# GNOME system apps whose Tela icons are flat; every saturated color in them
# becomes the accent. Third-party apps (Blender, Firefox...) keep their colors.
SYSTEM_APPS = ["org.gnome.Nautilus", "org.gnome.Settings", "org.gnome.Terminal", "org.gnome.Console",
               "org.gnome.Calculator", "org.gnome.TextEditor", "org.gnome.SystemMonitor",
               "org.gnome.Software", "org.gnome.Calendar", "org.gnome.clocks", "org.gnome.Weather",
               "org.gnome.Logs", "org.gnome.Extensions", "org.gnome.FileRoller", "org.gnome.Characters"]
HEX = re.compile(rb"#([0-9a-fA-F]{6})\b")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def saturated(hex_color):
    """True for real colors; grays, near-whites and near-blacks are left alone."""
    lightness, saturation = colorsys.rgb_to_hls(*(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)))[1:]
    return saturation > 0.25 and 0.12 < lightness < 0.92

# Screen crossfade from extension/ (GNOME's dark style transition): hold the old
# screen this long, then fade it out. Icons of an already built palette land within it.
EXTENSION_UUID = "paletti@yyg27"
TRANSITION_MS = (350, 800)

KEYBINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/paletti/"


def names():
    return sorted(p.stem for p in PALETTES.glob("*.toml"))


def load(name):
    """Checks everything apply needs up front, so a bad file never half-applies a theme."""
    palette = tomllib.loads((PALETTES / f"{name}.toml").read_text())
    colors = palette.get("colors", {})
    terminal = palette.get("terminal", {}).get("palette", [])
    problems = [f"missing {key}" for key in ("name", "gnome_accent", "icon_color") if key not in palette]
    if not isinstance(palette.get("icon_color", []), list):
        problems.append('icon_color must be a list, like ["green"]')
    problems += [f"missing color role {role}" for role in ROLES if role not in colors]
    problems += [f"{role} = {value!r} is not #rrggbb" for role, value in colors.items()
                 if not HEX_COLOR.fullmatch(str(value))]
    if len(terminal) != 16 or not all(HEX_COLOR.fullmatch(str(value)) for value in terminal):
        problems.append("[terminal] palette needs 16 #rrggbb colors")
    if problems:
        message = f"{name}.toml: {'; '.join(problems)}"
        sh("notify-send", "-a", "Paletti", "Paletti palette error", message)  # shortcuts have no terminal
        sys.exit(message)
    return palette


def render(palette, template="shell.css"):
    c = palette["colors"]
    # Window buttons: close in the palette's own red, the others in its signature colors.
    minimize = c["heading"] if c["heading"].lower() != c["accent"].lower() else c["accent2"]
    values = {"name": palette["name"], **c,
              "close": palette["terminal"]["palette"][1], "minimize": minimize, "maximize": c["accent"],
              "on_accent": c.get("on_accent", c["bg"]),  # text on accent fills, bg unless the palette says otherwise
              **{f"ansi{i}": color for i, color in enumerate(palette["terminal"]["palette"])}}
    for role, hex_color in palette["colors"].items():
        h = hex_color.lstrip("#")
        values[f"{role}_rgb"] = ", ".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return Template((HERE / template).read_text()).substitute(values)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def ext_gset(uuid, schema, key, value):
    """Extension schemas are not installed system-wide, so --schemadir is required."""
    schemas = EXTENSIONS / uuid / "schemas"
    if schemas.is_dir():
        sh("gsettings", "--schemadir", str(schemas), "set", schema, key, value)


def find_icon_theme(name):
    return next((d / name for d in ICON_DIRS if (d / name).is_dir()), None)


def set_icon_theme(name):
    # Switching away and back makes running apps reload their icons. Without the
    # pause GNOME merges both changes into "unchanged" and keeps the old icons.
    # The real name is kept on disk meanwhile: a job stopped mid-swap leaves
    # Adwaita behind, and the next job reads it back from there.
    ICON_THEME_SWAP.parent.mkdir(parents=True, exist_ok=True)
    ICON_THEME_SWAP.write_text(name)
    sh("gsettings", "set", "org.gnome.desktop.interface", "icon-theme", "Adwaita")
    time.sleep(0.5)
    sh("gsettings", "set", "org.gnome.desktop.interface", "icon-theme", name)
    ICON_THEME_SWAP.unlink()


def mix(hex_color, accent, amount):
    """Moves hex_color (no #) toward accent (with #) by amount (0..1)."""
    return "".join(f"{round(int(hex_color[i:i + 2], 16) * (1 - amount) + int(accent[1 + i:3 + i], 16) * amount):02x}"
                   for i in (0, 2, 4))


def gray(hex_color):
    """hex_color (no #) as the gray of its own brightness, so icon shapes stay readable."""
    level = round(sum(w * int(hex_color[i:i + 2], 16) for w, i in ((0.299, 0), (0.587, 2), (0.114, 4))))
    return f"{level:02x}" * 3


def tela_name(accent, tint, grayscale=False):
    return f"Tela-circle-paletti-{accent[1:].lower()}-t{round(tint * 100)}" + ("-gray" if grayscale else "")


def copy_tela(src, dst, links_from, copy_function):
    """dst.partial as a copy of src, hidden from icon theme pickers, with -dark's
    links into the light theme (../../Tela-circle/16/apps) pointed at dst's own.
    The caller renames it to dst when done: a job stopped halfway leaves only
    .partial, which the next job starts over, never a finished-looking broken copy."""
    tmp = dst.with_name(dst.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, symlinks=True, copy_function=copy_function)
    index = tmp / "index.theme"
    text = index.read_text()
    index.unlink()  # may be a hard link into the base copy
    text = re.sub(r"^Name=.*$", f"Name={dst.name}", text, count=1, flags=re.MULTILINE)
    index.write_text(text.replace("[Icon Theme]", "[Icon Theme]\nHidden=true", 1))
    base_link = re.compile(rf"^((?:\.\./)+){re.escape(links_from)}/")
    links_to = dst.name.removesuffix("-dark")
    for dirpath, dirnames, filenames in os.walk(tmp):  # links land in both lists
        for name in dirnames + filenames:
            link = Path(dirpath) / name
            if link.is_symlink() and base_link.match(os.readlink(link)):
                target = base_link.sub(rf"\g<1>{links_to}/", os.readlink(link))
                link.unlink()
                link.symlink_to(target)
    return tmp


def recolor_tela(accent, tint=0.0, grayscale=False):
    """Builds tela_name(accent, tint)(-dark): the installed Tela-circle with its blue
    replaced by the accent and app icons tinted toward it. Each is built once and
    kept, so switching back to a palette only changes the theme name. Files left
    as they are hard links into one shared base copy, so each costs ~8 MB."""
    if not (TELA_SRC / "Tela-circle").is_dir():
        return "Tela-circle not found, install tela-circle-icon-theme-standard."
    # Built themes are copies of the installed Tela. A package update gives index.theme
    # a new mtime; then they all go and are rebuilt as palettes are used.
    stamp = ICON_DIRS[0] / "Tela-circle-paletti-base/paletti-source"
    source = str((TELA_SRC / "Tela-circle/index.theme").stat().st_mtime_ns)
    if not (stamp.exists() and stamp.read_text() == source):
        for path in ICON_DIRS[0].glob("Tela-circle-paletti-*"):
            shutil.rmtree(path)
    for variant in ["", "-dark"]:
        src = TELA_SRC / f"Tela-circle{variant}"
        if not src.is_dir():
            return "Tela-circle not found, install tela-circle-icon-theme-standard."
        dst = ICON_DIRS[0] / f"{tela_name(accent, tint, grayscale)}{variant}"
        if dst.exists():
            continue
        base = ICON_DIRS[0] / f"Tela-circle-paletti-base{variant}"
        if not base.exists():
            tmp = copy_tela(src, base, "Tela-circle", shutil.copy2)
            if not variant:
                (tmp / "paletti-source").write_text(source)
            tmp.rename(base)
        tmp = copy_tela(base, dst, "Tela-circle-paletti-base", os.link)

        def put(rel, data):
            (tmp / rel).unlink()  # a hard link: writing in place would change the base
            (tmp / rel).write_bytes(data)

        for dirpath, _, filenames in os.walk(src):
            for filename in filenames:
                source = Path(dirpath) / filename
                if source.is_symlink() or not filename.endswith(".svg"):
                    continue
                data = source.read_bytes()
                if TELA_BLUE.search(data):
                    put(source.relative_to(src), TELA_BLUE.sub(accent.encode(), data))
        # App icons: a light tint toward the accent, or every color as its own gray.
        apps = src / "scalable/apps"
        if (tint or grayscale) and apps.is_dir() and apps.resolve().is_relative_to(src.resolve()):  # -dark links it to the base
            recolor = (lambda h: gray(h)) if grayscale else (lambda h: mix(h, accent, tint))
            for source in apps.glob("*.svg"):
                if source.is_symlink():
                    continue
                data = TELA_BLUE.sub(accent.encode(), source.read_bytes())
                put(source.relative_to(src), HEX.sub(lambda m: b"#" + recolor(m.group(1).decode().lower()).encode(), data))
        # System app icons: recolor the real file, so every alias name links to it.
        for app in SYSTEM_APPS:
            icon = src / "scalable/apps" / f"{app}.svg"
            if icon.exists() and icon.resolve().is_relative_to(src.resolve()):
                real = icon.resolve()
                data = HEX.sub(lambda m: accent.encode() if saturated(m.group(1).decode()) else m.group(0),
                               real.read_bytes())
                put(real.relative_to(src.resolve()), data)
        tmp.rename(dst)
    return None


def recolor_icons(colors, accent, tint=0.0, grayscale=False):
    """Recolors the active icon theme; returns a message when that is not possible."""
    theme = sh("gsettings", "get", "org.gnome.desktop.interface", "icon-theme").strip("'")
    if theme == "Adwaita" and ICON_THEME_SWAP.exists():  # an earlier job was stopped mid-swap
        theme = ICON_THEME_SWAP.read_text().strip()
    if theme.lower().startswith("adwaita"):
        return None  # Adwaita folders already follow the GNOME accent color.
    if theme in ("Tela-circle", "Tela-circle-dark") or theme.startswith("Tela-circle-paletti"):
        message = recolor_tela(accent, tint, grayscale)
        new = tela_name(accent, tint, grayscale) + ("-dark" if theme.endswith("dark") else "")
        if message is None and new != theme:
            # A new name reloads icons by itself, no Adwaita swap needed.
            sh("gsettings", "set", "org.gnome.desktop.interface", "icon-theme", new)
        return message
    path = find_icon_theme(theme)
    if path is None:
        return f"Icon theme {theme} not found, icons unchanged."

    # Papirus: a single theme whose folder color is picked by symlinks
    # (folder.svg -> folder-violet.svg). Checked first, since a Papirus theme
    # name can itself contain a color word.
    root = path.resolve().parent
    probe = root / "Papirus/48x48/places/folder.svg"
    if probe.is_symlink():
        current = re.match(r"folder-(\w+)\.svg", os.readlink(probe)).group(1)
        new = next((c for c in colors if (probe.parent / f"folder-{c}.svg").exists()), None)
        if new is None:
            return f"Papirus has no {'/'.join(colors)} folders, icons unchanged."
        if not os.access(root, os.W_OK):
            return f"{root} is not writable (system-wide Papirus), icons unchanged."
        if new != current:
            pattern = re.compile(rf"(^|/)((?:folder|user)-){current}([-.])")
            # Links named after a color (folder-green-desktop.svg) must keep pointing
            # at that color; only the generic ones (folder.svg, user-home.svg) move.
            papirus_colors = {f.name[7:-4] for f in probe.parent.glob("folder-*.svg")
                              if (probe.parent / f"folder-{f.name[7:-4]}-documents.svg").exists()}
            colored_name = re.compile(rf"^(?:folder|user)-(?:{'|'.join(map(re.escape, papirus_colors))})[-.]")
            for dirpath, _, filenames in os.walk(root):  # file symlinks land in filenames
                for filename in filenames:
                    link = Path(dirpath) / filename
                    if not link.is_symlink() or colored_name.match(filename):
                        continue
                    target = os.readlink(link)
                    new_target = pattern.sub(rf"\g<1>\g<2>{new}\g<3>", target)
                    if new_target != target and (link.parent / new_target).exists():
                        # Atomic swap: an interrupted run never leaves a broken link.
                        tmp = link.with_name(f".{filename}.paletti")
                        tmp.unlink(missing_ok=True)
                        tmp.symlink_to(new_target)
                        os.replace(tmp, link)
            set_icon_theme(theme)
        return None

    # Color variants installed as separate themes: Tela-purple-dark, Colloid-Green-Dark, ...
    match = re.search("|".join(ICON_COLORS), theme, re.IGNORECASE)
    if match:
        for color in colors:
            word = color.capitalize() if match.group()[0].isupper() else color
            candidate = theme[:match.start()] + word + theme[match.end():]
            if find_icon_theme(candidate):
                if candidate != theme:
                    set_icon_theme(candidate)
                return None
    # shortcut: themes naming colors differently (Qogir-manjaro = green) are left unchanged, add a name map when someone uses one.
    return f"Icon theme {theme} has no {'/'.join(colors)} variant installed, icons unchanged."


def apply(name):
    palette = load(name)
    c = palette["colors"]

    # Shell: write the CSS, then the extension freezes the screen and reloads the theme.
    THEME_CSS.parent.mkdir(parents=True, exist_ok=True)
    THEME_CSS.write_text(render(palette))
    user_theme = ("user-theme@gnome-shell-extensions.gcampax.github.com",
                  "org.gnome.shell.extensions.user-theme", "name")
    ext_gset(*user_theme, "Islands")  # a no-op unless another theme was set
    reloaded = sh("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/io/paletti/Transition",
                  "--method", "io.paletti.Transition.Reload", *map(str, TRANSITION_MS)) == "()"
    if not reloaded:  # no extension (or not loaded yet): reload by clearing and resetting user-theme
        ext_gset(*user_theme, "")
        ext_gset(*user_theme, "Islands")

    # Dock: color and running dots. Opacity is left alone, it is tuned by hand.
    dock = ("dash-to-dock@micxgx.gmail.com", "org.gnome.shell.extensions.dash-to-dock")
    ext_gset(*dock, "custom-background-color", "true")
    ext_gset(*dock, "background-color", c["bg"])
    ext_gset(*dock, "custom-theme-customize-running-dots", "true")
    ext_gset(*dock, "custom-theme-running-dots-color", c["accent"])
    ext_gset(*dock, "custom-theme-running-dots-border-color", c["accent"])

    # Blur my Shell draws its own blurred shapes behind the top bar, menus and dock.
    # They don't match the islands and corners above and show around them.
    for part in ["panel", "popup", "dash-to-dock"]:
        ext_gset("blur-my-shell@aunetx", f"org.gnome.shell.extensions.blur-my-shell.{part}", "blur", "false")

    # GNOME accent color and apps; newly opened apps pick up the new colors.
    sh("gsettings", "set", "org.gnome.desktop.interface", "accent-color", palette["gnome_accent"])
    for template, target in GTK_CSS.items():
        write_generated(target, render(palette, template))

    # Firefox draws its own window buttons; it reads userChrome.css on its next start.
    for profile in firefox_profiles():
        write_generated(profile / "chrome/userChrome.css", render(palette, "firefox.css"))
        user_js = profile / "user.js"
        pref = 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);'
        if pref not in (user_js.read_text() if user_js.exists() else ""):
            with user_js.open("a") as f:  # appended, the user's own prefs stay
                f.write(pref + "\n")

    apply_vscode(palette)
    apply_wallpaper(name, palette)

    # GNOME Terminal default profile.
    profile = sh("gsettings", "get", "org.gnome.Terminal.ProfilesList", "default").strip("'")
    if profile:
        base = f"/org/gnome/terminal/legacy/profiles:/:{profile}/"
        terminal = {
            "use-theme-colors": "false",
            "background-color": f"'{c['bg']}'",
            "foreground-color": f"'{c['fg']}'",
            "bold-color-same-as-fg": "true",
            "cursor-colors-set": "true",
            "cursor-background-color": f"'{c['fg']}'",
            "cursor-foreground-color": f"'{c['bg']}'",
            "highlight-colors-set": "true",
            "highlight-background-color": f"'{c['surface']}'",
            "highlight-foreground-color": f"'{c['fg']}'",
            "palette": str(palette["terminal"]["palette"]),
        }
        for key, value in terminal.items():
            sh("dconf", "write", base + key, value)

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(name)
    start_icons(name)


def apply_vscode(palette):
    """Sets VS Code's color theme in settings.json, which VS Code reloads by itself.
    A palette with a ready-made VS Code theme (vscode_theme) just switches to it;
    one without gets the built-in dark theme with paletti's interface and syntax
    colors on top. The user's other settings, colors and token rules stay; the
    original is backed up once."""
    ours = json.loads(render(palette, "vscode.json"))
    theme = palette.get("vscode_theme")
    for folder in VSCODE_DIRS:
        target = folder / "settings.json"
        if not target.exists():
            continue
        try:
            settings = json.loads(target.read_text() or "{}")
        except json.JSONDecodeError:
            settings = None
        if not isinstance(settings, dict):
            # shortcut: comments or trailing commas would be lost on a rewrite, so such a file is left alone; parse JSONC once someone has one.
            print(f"{target} is not plain JSON (comments?), VS Code colors unchanged.", file=sys.stderr)
            continue
        backup = target.with_name("settings.json.before-paletti")
        if not backup.exists():
            shutil.copy2(target, backup)
        # An earlier palette's colors go first, so a ready-made theme shows as it was made.
        colors = {k: v for k, v in settings.get("workbench.colorCustomizations", {}).items() if k not in ours["colors"]}
        tokens = settings.get("editor.tokenColorCustomizations", {})
        rules = [r for r in tokens.get("textMateRules", []) if not str(r.get("name", "")).startswith("paletti:")]
        semantic = settings.get("editor.semanticTokenColorCustomizations", {})
        semantic_rules = {k: v for k, v in semantic.get("rules", {}).items() if k not in ours["semantic"]}
        if not theme:
            colors.update(ours["colors"])
            rules = ours["tokens"] + rules  # the user's own rules come after, so they win
            semantic_rules.update(ours["semantic"])
            semantic.setdefault("enabled", True)  # the rules only apply with semantic highlighting on
        settings["workbench.colorTheme"] = theme or "Default Dark Modern"
        settings["workbench.colorCustomizations"] = colors
        settings["editor.tokenColorCustomizations"] = {**tokens, "textMateRules": rules}
        settings["editor.semanticTokenColorCustomizations"] = {**semantic, "rules": semantic_rules}
        # Whatever ends up empty is dropped, so the file stays as the user wrote it.
        for key, inner in [("editor.tokenColorCustomizations", "textMateRules"),
                           ("editor.semanticTokenColorCustomizations", "rules")]:
            if not settings[key][inner]:
                del settings[key][inner]
        for key in ["workbench.colorCustomizations", "editor.tokenColorCustomizations",
                    "editor.semanticTokenColorCustomizations"]:
            if not settings[key]:
                del settings[key]
        tmp = target.with_name(".settings.json.paletti")
        tmp.write_text(json.dumps(settings, indent=4) + "\n")
        os.replace(tmp, target)  # VS Code watches the file; it never sees half of it


def write_generated(target, text):
    """A file paletti did not write is backed up first, never over an earlier backup."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not target.read_text().startswith("/* paletti"):
        backup = target.with_name(f"{target.name}.before-paletti")
        if backup.exists():
            backup = target.with_name(f"{target.name}.before-paletti.{time.time_ns()}")
        target.rename(backup)
    target.write_text(text)


def firefox_profiles():
    """The profile each Firefox install starts with (Default in installs.ini)."""
    profiles = []
    for root in FIREFOX_DIRS:
        installs = configparser.ConfigParser()
        installs.read(root / "installs.ini")
        profiles += [root / s["Default"] for s in installs.values() if "Default" in s and (root / s["Default"]).is_dir()]
    return profiles


def start_icons(name):
    """Icons take ~1s, so they run in a detached process after everything else
    has changed. A newer switch stops the older job: the latest palette wins,
    and since every job rewrites all icons, a cut-off job leaves nothing behind."""
    try:
        pid = int(ICONS_PID.read_text())
        # Only stop it if that pid is still our job; pids get reused by other programs.
        if b"--icons" in Path(f"/proc/{pid}/cmdline").read_bytes():
            os.kill(pid, signal.SIGTERM)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    job = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--icons", name],
                           start_new_session=True, stdout=subprocess.DEVNULL)
    ICONS_PID.write_text(str(job.pid))


def icon_args(palette):
    """recolor_icons' accent, tint and grayscale. Icons follow the accent unless
    the palette gives them their own color."""
    c = palette["colors"]
    return c.get("icons", c["accent"]), palette.get("icon_tint", 0.0), palette.get("icon_grayscale", False)


def prune_tela():
    """Deletes built Tela themes no palette uses anymore (an old accent or tint)."""
    try:
        wanted = {tela_name(*icon_args(tomllib.loads((PALETTES / f"{n}.toml").read_text()))) for n in names()}
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return  # a broken palette may still want its theme; load reports it on its switch
    wanted.add("Tela-circle-paletti-base")
    for path in ICON_DIRS[0].glob("Tela-circle-paletti-*"):
        if path.name.removesuffix(".partial").removesuffix("-dark") not in wanted:
            shutil.rmtree(path)


def apply_icons(name):
    palette = load(name)
    message = recolor_icons(palette["icon_color"], *icon_args(palette))
    prune_tela()
    if message:
        print(message, file=sys.stderr)
    # Last, so it shows up once everything, icons included, has switched. This job
    # runs detached, so the notification is the only place an icon problem is seen.
    sh("notify-send", "--transient", "-a", "Paletti", "-t", "3000", palette["name"],
       message or palette.get("description", ""))


def load_config():
    """None when the file is broken; the error is shown, since shortcuts have no terminal."""
    try:
        return tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    except tomllib.TOMLDecodeError as e:
        message = f"{CONFIG} is not valid TOML, wallpaper left alone: {e}"
        print(message, file=sys.stderr)
        sh("notify-send", "-a", "Paletti", "Paletti config error", message)
        return None


def save_config(config):
    # shortcut: rewrites the file, so hand-written comments in it are lost; keep them once someone asks.
    lines = [f'wallpaper = "{config.get("wallpaper", "default")}"   # default | chosen | keep', "",
             "[wallpapers]   # your own wallpapers per palette, cycled with Super+Shift+Y"]
    lines += [f"{name} = {json.dumps(paths)}" for name, paths in config.get("wallpapers", {}).items()]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text("\n".join(lines) + "\n")


def wallpaper_options(name, palette, config):
    """The palette's default wallpaper first, then the user's own. Relative paths
    are inside the project, so the bundled wallpapers work from any checkout."""
    paths = ([palette["wallpaper"]] if "wallpaper" in palette else []) + config.get("wallpapers", {}).get(name, [])
    options = []
    for p in paths:
        path = Path(p).expanduser()
        path = path if path.is_absolute() else HERE / path
        if path.is_file():
            options.append(path)
        else:
            print(f"Wallpaper {path} not found, skipped.", file=sys.stderr)
    return options


def pick_wallpaper(options, mode, chosen):
    """None means: leave the wallpaper alone."""
    if mode == "keep" or not options:
        return None
    if mode == "chosen" and chosen in options:
        return chosen
    return options[0]


def set_wallpaper(path):
    """Desktop (light and dark style) and lock screen."""
    for schema, key in [("org.gnome.desktop.background", "picture-uri"),
                        ("org.gnome.desktop.background", "picture-uri-dark"),
                        ("org.gnome.desktop.screensaver", "picture-uri")]:
        sh("gsettings", "set", schema, key, path.as_uri())


def chosen_wallpaper(name):
    path = CHOSEN_WALLPAPER / name
    return Path(path.read_text().strip()) if path.exists() else None


def apply_wallpaper(name, palette):
    config = load_config()
    if config is None:
        return  # the theme switch goes on without it
    options = wallpaper_options(name, palette, config)
    wallpaper = pick_wallpaper(options, config.get("wallpaper", "default"), chosen_wallpaper(name))
    if wallpaper:
        set_wallpaper(wallpaper)


def saved_name():
    return STATE.read_text().strip() if STATE.exists() else None


def current_name():
    name = saved_name()
    if name not in names():
        sys.exit("No palette applied yet, run paletti first.")
    return name


def next_wallpaper():
    """Cycles the current palette's wallpapers and remembers the pick."""
    name = current_name()
    config = load_config()
    if config is None:
        sys.exit(1)  # saving now would overwrite the user's wallpaper list
    palette = load(name)
    options = wallpaper_options(name, palette, config)
    if not options:
        sys.exit(f"{name} has no wallpapers; add some under [wallpapers] in {CONFIG}.")
    current = pick_wallpaper(options, "chosen", chosen_wallpaper(name))
    wallpaper = options[(options.index(current) + 1) % len(options)]
    CHOSEN_WALLPAPER.mkdir(parents=True, exist_ok=True)
    (CHOSEN_WALLPAPER / name).write_text(str(wallpaper))
    config["wallpaper"] = "chosen"
    save_config(config)
    set_wallpaper(wallpaper)
    title = wallpaper.stem.replace("_", " ").replace("-", " ").title()
    sh("notify-send", "--transient", "-a", "Paletti", "-t", "3000", "Wallpaper changed", title)


def set_wallpaper_mode(mode):
    config = load_config()
    if config is None:
        sys.exit(1)  # saving now would overwrite the user's wallpaper list
    config["wallpaper"] = mode
    save_config(config)
    name = current_name()
    apply_wallpaper(name, load(name))
    print(f"Wallpaper mode: {mode}")


def next_name():
    all_names = names()
    current = saved_name()
    i = all_names.index(current) if current in all_names else -1
    return all_names[(i + 1) % len(all_names)]


def locked():
    """One switch at a time: a quick second Super+Y waits for this one instead of
    reading the same current palette and racing it."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK.open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock  # closing it releases the lock


def install():
    link = HOME / ".local/bin/paletti"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():  # re-pointed, so a moved checkout keeps working
        link.unlink()
    if not link.exists():
        link.symlink_to(Path(__file__).resolve())

    # The transition extension. A newly installed extension loads after logging
    # out and back in, so it is enabled through gsettings.
    extension = EXTENSIONS / EXTENSION_UUID
    if extension.is_symlink():
        extension.unlink()
    if not extension.exists():
        extension.symlink_to(HERE / "extension")
    enabled = ast.literal_eval(sh("gsettings", "get", "org.gnome.shell", "enabled-extensions").removeprefix("@as "))
    if EXTENSION_UUID not in enabled:
        sh("gsettings", "set", "org.gnome.shell", "enabled-extensions", str(enabled + [EXTENSION_UUID]))

    # Existing custom shortcuts are kept, only paletti's are added.
    shortcuts = [(KEYBINDING_PATH, "Paletti", str(link), "<Super>y"),
                 (KEYBINDING_PATH.replace("/paletti/", "/paletti-wallpaper/"), "Paletti wallpaper",
                  f"{link} --next-wallpaper", "<Super><Shift>y")]
    paths = ast.literal_eval(sh("gsettings", "get", KEYBINDING_SCHEMA, "custom-keybindings").removeprefix("@as "))
    new_paths = [path for path, *_ in shortcuts if path not in paths]
    if new_paths:
        sh("gsettings", "set", KEYBINDING_SCHEMA, "custom-keybindings", str(paths + new_paths))
    for path, name, command, binding in shortcuts:
        item = f"{KEYBINDING_SCHEMA}.custom-keybinding:{path}"
        sh("gsettings", "set", item, "name", name)
        sh("gsettings", "set", item, "command", command)
        sh("gsettings", "set", item, "binding", binding)
    print(f"Installed: {link}, shortcuts Super+Y (theme) and Super+Shift+Y (wallpaper)")
    print(f"Extension {EXTENSION_UUID} enabled; log out and back in once for the screen transition.")
    install_vscode_themes()


def swatches(name):
    """A palette's accent colors; none for a broken file, the menu still lists it."""
    try:
        c = tomllib.loads((PALETTES / f"{name}.toml").read_text()).get("colors", {})
    except tomllib.TOMLDecodeError:
        return []
    # Checked, since the extension puts them into a style string; a repeated color shows once.
    return list(dict.fromkeys(c[role].lower() for role in ("accent", "accent2", "heading")
                              if HEX_COLOR.fullmatch(str(c.get(role)))))


def install_vscode_themes():
    """The Marketplace themes palettes switch VS Code to, for each VS Code found."""
    wanted = {load(name).get("vscode_extension") for name in names()} - {None}
    for cli in ["code", "codium"]:
        if not shutil.which(cli):
            continue
        have = set(sh(cli, "--list-extensions").lower().split())
        for extension in sorted(wanted - have):
            out = sh(cli, "--install-extension", extension)
            print(out.splitlines()[-1] if "successfully" in out else f"{cli}: could not install {extension}")


def gdm_patched(path):
    from gi.repository import Gio
    css = Gio.Resource.load(str(path)).lookup_data("/org/gnome/shell/theme/gnome-shell-dark.css", 0)
    return GDM_MARKER in css.get_data()


def save_gdm_original():
    """Keeps GNOME's theme resource as shipped: the base every palette is applied to."""
    if not gdm_patched(GDM_GRESOURCE):
        GDM_ORIGINAL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(GDM_GRESOURCE, GDM_ORIGINAL)


def read_palette_name(path):
    """The palette name in the user's state file, read as root: no following a link,
    no blocking, at most 100 bytes, and anything but a known palette is ignored."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            name = os.read(fd, 100).decode(errors="replace").strip()
        finally:
            os.close(fd)
    except OSError:
        return None
    return name if name in names() else None


def build_gdm_gresource(original, palette, out):
    """out: GNOME's theme resource with gdm.css appended to its stylesheets and the
    palette's wallpaper, blurred, as the login background."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gio
    prefix = "/org/gnome/shell/theme/"
    resource = Gio.Resource.load(str(original))
    css = render(palette, "gdm.css").encode()
    with tempfile.TemporaryDirectory() as tmp:
        tmp, files = Path(tmp), []

        def copy(path):
            for child in resource.enumerate_children(prefix + path, 0):
                if child.endswith("/"):
                    copy(path + child)
                    continue
                data = resource.lookup_data(prefix + path + child, 0).get_data()
                if child.startswith("gnome-shell") and child.endswith(".css") and "high-contrast" not in child:
                    data += css
                (tmp / path).mkdir(parents=True, exist_ok=True)
                (tmp / path / child).write_bytes(data)
                files.append(path + child)
        copy("")

        wallpaper = (HERE / palette.get("wallpaper", "-")).resolve()
        if wallpaper.is_file() and wallpaper.is_relative_to((HERE / "wallpapers").resolve()):
            bilinear = GdkPixbuf.InterpType.BILINEAR
            image = GdkPixbuf.Pixbuf.new_from_file(str(wallpaper))
            width = 1920
            height = max(32, round(image.get_height() * width / image.get_width()))
            # A cheap blur without an image library: shrink, then grow back in doubling
            # steps (one big step comes out blocky).
            image = image.scale_simple(width // 32, height // 32, bilinear)
            while image.get_width() < width:
                step = min(width, image.get_width() * 2)
                image = image.scale_simple(step, round(height * step / width), bilinear)
            # Darkened toward the palette background, so the login text stays readable.
            background = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
            background.fill(int(palette["colors"]["bg"][1:] + "ff", 16))
            image.composite(background, 0, 0, width, height, 0, 0, 1, 1, bilinear, 140)
            background.savev(str(tmp / "paletti-gdm.png"), "png", [], [])
            files.append("paletti-gdm.png")

        (tmp / "theme.xml").write_text('<?xml version="1.0"?><gresources><gresource prefix="/org/gnome/shell/theme">'
                                       + "".join(f"<file>{escape(f)}</file>" for f in files)
                                       + "</gresource></gresources>")
        subprocess.run(["glib-compile-resources", "--sourcedir", str(tmp), "--target", str(out),
                        str(tmp / "theme.xml")], check=True)
    # Checked before GDM ever loads it: a broken file would leave the login screen without a theme.
    if not gdm_patched(out):
        raise RuntimeError(f"{out} came out without the palette's stylesheet")


def apply_gdm(palette):
    """Runs as root: swaps in GNOME's theme resource with the palette's login screen."""
    tmp = GDM_GRESOURCE.with_name(".paletti-gdm.gresource")
    try:
        build_gdm_gresource(GDM_ORIGINAL, palette, tmp)
        tmp.chmod(0o644)
        os.replace(tmp, GDM_GRESOURCE)  # atomic: running shells keep the file they mapped
    finally:
        tmp.unlink(missing_ok=True)


def gdm_setup():
    if os.geteuid() != 0 or os.environ.get("SUDO_USER", "root") == "root":
        sys.exit("Run it with sudo from your own account: sudo ~/.local/bin/paletti --gdm-setup")
    state = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir) / ".local/state/paletti/current"
    if not GDM_ORIGINAL.exists() and gdm_patched(GDM_GRESOURCE):
        sys.exit(f"{GDM_GRESOURCE} is patched and its original is gone; reinstall gnome-shell first.")
    save_gdm_original()

    # The root copy: the service and hook run and read only this, never the user's checkout.
    shutil.rmtree(GDM_HOME, ignore_errors=True)
    GDM_HOME.mkdir(parents=True)
    for name in ["paletti.py", "gdm.css"]:
        shutil.copy2(HERE / name, GDM_HOME / name)
    for name in ["palettes", "wallpapers"]:
        shutil.copytree(HERE / name, GDM_HOME / name, symlinks=True)  # links out are refused at apply

    run = f"/usr/bin/python3 -I {GDM_HOME / 'paletti.py'}"
    (GDM_UNITS / "paletti-gdm.path").write_text(f"""[Unit]
Description=Update the GDM login screen when paletti switches palettes

[Path]
PathChanged={state}

[Install]
WantedBy=multi-user.target
""")
    (GDM_UNITS / "paletti-gdm.service").write_text(f"""[Unit]
Description=Apply the current paletti palette to the GDM login screen

[Service]
Type=oneshot
ExecStart={run} --gdm-apply {state}
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths={GDM_GRESOURCE.parent}
""")
    GDM_HOOK.parent.mkdir(parents=True, exist_ok=True)
    GDM_HOOK.write_text(f"""[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Target = {str(GDM_GRESOURCE).lstrip("/")}

[Action]
Description = Applying the paletti palette to the GDM login screen...
When = PostTransaction
Exec = {run} --gdm-refresh {state}
""")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "paletti-gdm.path"], check=True)
    subprocess.run(["systemctl", "start", "paletti-gdm.service"], check=True)
    print("GDM now follows the palette; it shows on the next login screen.\n"
          "After changing palettes or their wallpapers, run this again. Undo: sudo ~/.local/bin/paletti --gdm-remove")


def gdm_remove():
    if os.geteuid() != 0:
        sys.exit("Run it with sudo: sudo ~/.local/bin/paletti --gdm-remove")
    subprocess.run(["systemctl", "disable", "--now", "paletti-gdm.path"])
    for path in [GDM_UNITS / "paletti-gdm.path", GDM_UNITS / "paletti-gdm.service", GDM_HOOK]:
        path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reload"])
    if GDM_ORIGINAL.exists():
        tmp = GDM_GRESOURCE.with_name(".paletti-gdm.gresource")
        shutil.copy2(GDM_ORIGINAL, tmp)
        os.replace(tmp, GDM_GRESOURCE)
    shutil.rmtree(GDM_HOME, ignore_errors=True)
    shutil.rmtree(GDM_ORIGINAL.parent, ignore_errors=True)
    print("GDM is back to GNOME's own theme.")


def main(args):
    if args == ["--list"]:
        print("\n".join(names()))
    elif args == ["--list-colors"]:  # for the Quick Settings menu's swatches
        print(json.dumps({name: swatches(name) for name in names()}))
    elif args == ["--install"]:
        install()
    elif len(args) == 2 and args[0] == "--icons" and args[1] in names():  # internal, see start_icons
        apply_icons(args[1])
    elif args == ["--gdm-setup"]:
        gdm_setup()
    elif args == ["--gdm-remove"]:
        gdm_remove()
    elif len(args) == 2 and args[0] in ("--gdm-apply", "--gdm-refresh"):  # internal: the root service and hook
        if args[0] == "--gdm-refresh":
            save_gdm_original()
        name = read_palette_name(Path(args[1]))
        if name:
            apply_gdm(load(name))
    elif args == ["--next-wallpaper"]:
        next_wallpaper()
    elif len(args) == 2 and args[0] == "--wallpaper" and args[1] in WALLPAPER_MODES:
        set_wallpaper_mode(args[1])
    elif not args or (len(args) == 1 and args[0] in names()):
        with locked():
            apply(args[0] if args else next_name())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
