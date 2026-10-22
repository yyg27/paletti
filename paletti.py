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
"""

import ast
import colorsys
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from string import Template

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

GTK_CSS = {"gtk4.css": HOME / ".config/gtk-4.0/gtk.css",
           "gtk3.css": HOME / ".config/gtk-3.0/gtk.css"}

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
# screen this long, then fade it out. Icons land ~1s in, near the end of the fade.
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
              "on_accent": c.get("on_accent", c["bg"])}  # text on accent fills, bg unless the palette says otherwise
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


def recolor_tela(accent, tint=0.0):
    """Writes Tela-circle-paletti(-dark): the installed Tela-circle with its blue
    replaced by the accent and app icons tinted toward it. Always rewritten from
    the source, so colors never drift across theme switches."""
    for variant in ["", "-dark"]:
        src = TELA_SRC / f"Tela-circle{variant}"
        dst = ICON_DIRS[0] / f"Tela-circle-paletti{variant}"
        if not src.is_dir():
            return "Tela-circle not found, install tela-circle-icon-theme-standard."
        if not dst.exists():
            tmp = dst.with_name(dst.name + ".partial")
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp, symlinks=True)
            # Fixed up before the rename: a job stopped halfway leaves only .partial,
            # which the next job starts over, never a finished-looking broken copy.
            index = tmp / "index.theme"
            index.write_text(index.read_text().replace("Name=Tela circle", "Name=Tela circle paletti", 1))
            # -dark links into the base theme at any depth (../../Tela-circle/16/apps).
            base_link = re.compile(r"^((?:\.\./)+)Tela-circle/")
            for dirpath, dirnames, filenames in os.walk(tmp):  # links land in both lists
                for name in dirnames + filenames:
                    link = Path(dirpath) / name
                    if link.is_symlink() and base_link.match(os.readlink(link)):
                        target = base_link.sub(r"\1Tela-circle-paletti/", os.readlink(link))
                        link.unlink()
                        link.symlink_to(target)
            tmp.rename(dst)
        for dirpath, _, filenames in os.walk(src):
            for filename in filenames:
                source = Path(dirpath) / filename
                if source.is_symlink() or not filename.endswith(".svg"):
                    continue
                data = source.read_bytes()
                if TELA_BLUE.search(data):
                    (dst / source.relative_to(src)).write_bytes(TELA_BLUE.sub(accent.encode(), data))
        # App icons: a light tint toward the accent. Rewritten even at tint 0, so a
        # previous theme's tint never stays behind.
        apps = src / "scalable/apps"
        if apps.is_dir() and apps.resolve().is_relative_to(src.resolve()):  # -dark links it to the base
            for source in apps.glob("*.svg"):
                if source.is_symlink():
                    continue
                data = TELA_BLUE.sub(accent.encode(), source.read_bytes())
                if tint:
                    data = HEX.sub(lambda m: b"#" + mix(m.group(1).decode().lower(), accent, tint).encode(), data)
                (dst / source.relative_to(src)).write_bytes(data)
        # System app icons: recolor the real file, so every alias name links to it.
        for app in SYSTEM_APPS:
            icon = src / "scalable/apps" / f"{app}.svg"
            if icon.exists() and icon.resolve().is_relative_to(src.resolve()):
                real = icon.resolve()
                data = HEX.sub(lambda m: accent.encode() if saturated(m.group(1).decode()) else m.group(0),
                               real.read_bytes())
                (dst / real.relative_to(src.resolve())).write_bytes(data)
    return None


def recolor_icons(colors, accent, tint=0.0):
    """Recolors the active icon theme; returns a message when that is not possible."""
    theme = sh("gsettings", "get", "org.gnome.desktop.interface", "icon-theme").strip("'")
    if theme == "Adwaita" and ICON_THEME_SWAP.exists():  # an earlier job was stopped mid-swap
        theme = ICON_THEME_SWAP.read_text().strip()
    if theme.lower().startswith("adwaita"):
        return None  # Adwaita folders already follow the GNOME accent color.
    if theme in ("Tela-circle", "Tela-circle-dark", "Tela-circle-paletti", "Tela-circle-paletti-dark"):
        message = recolor_tela(accent, tint)
        if message is None:
            set_icon_theme("Tela-circle-paletti-dark" if theme.endswith("dark") else "Tela-circle-paletti")
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
    # manbun: themes naming colors differently (Qogir-manjaro = green) need a name map, add when someone uses one.
    return f"Icon theme {theme} has no {'/'.join(colors)} variant installed, icons unchanged."


def apply(name):
    palette = load(name)
    c = palette["colors"]

    # Freeze the current screen first; without the extension this just fails quietly.
    sh("gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path", "/io/paletti/Transition",
       "--method", "io.paletti.Transition.Freeze", *map(str, TRANSITION_MS))

    # Shell: write the CSS, then reload by clearing and resetting user-theme.
    THEME_CSS.parent.mkdir(parents=True, exist_ok=True)
    THEME_CSS.write_text(render(palette))
    user_theme = ("user-theme@gnome-shell-extensions.gcampax.github.com",
                  "org.gnome.shell.extensions.user-theme", "name")
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

    # GNOME accent color and apps. A gtk.css paletti did not write is backed up,
    # never over an earlier backup; newly opened apps pick up the new colors.
    sh("gsettings", "set", "org.gnome.desktop.interface", "accent-color", palette["gnome_accent"])
    for template, target in GTK_CSS.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not target.read_text().startswith("/* paletti"):
            backup = target.with_name("gtk.css.before-paletti")
            if backup.exists():
                backup = target.with_name(f"gtk.css.before-paletti.{time.time_ns()}")
            target.rename(backup)
        target.write_text(render(palette, template))

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


def apply_icons(name):
    palette = load(name)
    # Icons follow the accent unless the palette gives them their own color.
    c = palette["colors"]
    message = recolor_icons(palette["icon_color"], c.get("icons", c["accent"]), palette.get("icon_tint", 0.0))
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
    # manbun: rewrites the file, so hand-written comments in it are lost.
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


def current_name():
    name = STATE.read_text().strip() if STATE.exists() else None
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
    current = STATE.read_text().strip() if STATE.exists() else None
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
    if not link.exists():
        link.symlink_to(Path(__file__).resolve())

    # The transition extension. A newly installed extension loads after logging
    # out and back in, so it is enabled through gsettings.
    extension = EXTENSIONS / EXTENSION_UUID
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


def main(args):
    if args == ["--list"]:
        print("\n".join(names()))
    elif args == ["--install"]:
        install()
    elif len(args) == 2 and args[0] == "--icons" and args[1] in names():  # internal, see start_icons
        apply_icons(args[1])
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
