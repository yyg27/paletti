#!/usr/bin/env python3
"""paletti: switch the GNOME desktop color theme with one command.

    paletti              switch to the next theme
    paletti everforest   switch to a specific theme
    paletti --list       list themes
    paletti --install    ~/.local/bin/paletti symlink + Super+Y shortcut
"""

import ast
import colorsys
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
PALETTES = HERE / "palettes"
HOME = Path.home()
EXTENSIONS = HOME / ".local/share/gnome-shell/extensions"
THEME_CSS = HOME / ".local/share/themes/Islands/gnome-shell/gnome-shell.css"
STATE = HOME / ".local/state/paletti/current"

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


def saturated(hex_color):
    """True for real colors; grays, near-whites and near-blacks are left alone."""
    lightness, saturation = colorsys.rgb_to_hls(*(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)))[1:]
    return saturation > 0.25 and 0.12 < lightness < 0.92

KEYBINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/paletti/"


def names():
    return sorted(p.stem for p in PALETTES.glob("*.toml"))


def load(name):
    palette = tomllib.loads((PALETTES / f"{name}.toml").read_text())
    missing = [r for r in ROLES if r not in palette["colors"]]
    if missing:
        sys.exit(f"{name}.toml is missing color roles: {', '.join(missing)}")
    return palette


def render(palette, template="shell.css"):
    values = {"name": palette["name"], **palette["colors"]}
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
    # Switching away and back makes running apps reload their icons.
    sh("gsettings", "set", "org.gnome.desktop.interface", "icon-theme", "Adwaita")
    sh("gsettings", "set", "org.gnome.desktop.interface", "icon-theme", name)


def recolor_tela(accent):
    """Writes Tela-circle-paletti(-dark): the installed Tela-circle with its blue
    replaced by the accent. Always rewritten from the source, so it never drifts."""
    for variant in ["", "-dark"]:
        src = TELA_SRC / f"Tela-circle{variant}"
        dst = ICON_DIRS[0] / f"Tela-circle-paletti{variant}"
        if not src.is_dir():
            return "Tela-circle not found, install tela-circle-icon-theme-standard."
        if not dst.exists():
            shutil.copytree(src, dst, symlinks=True)
            index = dst / "index.theme"
            index.write_text(index.read_text().replace("Name=Tela circle", "Name=Tela circle paletti", 1))
            # -dark links into the base theme at any depth (../../Tela-circle/16/apps).
            base_link = re.compile(r"^((?:\.\./)+)Tela-circle/")
            for dirpath, dirnames, filenames in os.walk(dst):  # links land in both lists
                for name in dirnames + filenames:
                    link = Path(dirpath) / name
                    if link.is_symlink() and base_link.match(os.readlink(link)):
                        target = base_link.sub(r"\1Tela-circle-paletti/", os.readlink(link))
                        link.unlink()
                        link.symlink_to(target)
        for dirpath, _, filenames in os.walk(src):
            for filename in filenames:
                source = Path(dirpath) / filename
                if source.is_symlink() or not filename.endswith(".svg"):
                    continue
                data = source.read_bytes()
                if TELA_BLUE.search(data):
                    (dst / source.relative_to(src)).write_bytes(TELA_BLUE.sub(accent.encode(), data))
        # System app icons: recolor the real file, so every alias name links to it.
        for app in SYSTEM_APPS:
            icon = src / "scalable/apps" / f"{app}.svg"
            if icon.exists() and icon.resolve().is_relative_to(src.resolve()):
                real = icon.resolve()
                data = HEX.sub(lambda m: accent.encode() if saturated(m.group(1).decode()) else m.group(0),
                               real.read_bytes())
                (dst / real.relative_to(src.resolve())).write_bytes(data)
    return None


def recolor_icons(colors, accent):
    """Recolors the active icon theme; returns a message when that is not possible."""
    theme = sh("gsettings", "get", "org.gnome.desktop.interface", "icon-theme").strip("'")
    if theme.lower().startswith("adwaita"):
        return None  # Adwaita folders already follow the GNOME accent color.
    if theme in ("Tela-circle", "Tela-circle-dark", "Tela-circle-paletti", "Tela-circle-paletti-dark"):
        message = recolor_tela(accent)
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

    # GNOME accent color and apps. A gtk.css that predates paletti is backed up
    # once; newly opened apps pick up the new colors.
    sh("gsettings", "set", "org.gnome.desktop.interface", "accent-color", palette["gnome_accent"])
    for template, target in GTK_CSS.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name("gtk.css.before-paletti")
        if target.exists() and not backup.exists() and not target.read_text().startswith("/* paletti"):
            target.rename(backup)
        target.write_text(render(palette, template))

    message = recolor_icons(palette["icon_color"], c["accent"])
    if message:
        print(message, file=sys.stderr)

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
    sh("notify-send", "-a", "Paletti", "-t", "2000", f"Theme: {palette['name']}")


def next_name():
    all_names = names()
    current = STATE.read_text().strip() if STATE.exists() else None
    i = all_names.index(current) if current in all_names else -1
    return all_names[(i + 1) % len(all_names)]


def install():
    link = HOME / ".local/bin/paletti"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(Path(__file__).resolve())

    # Existing custom shortcuts are kept, only paletti's is added.
    paths = ast.literal_eval(sh("gsettings", "get", KEYBINDING_SCHEMA, "custom-keybindings").removeprefix("@as "))
    if KEYBINDING_PATH not in paths:
        sh("gsettings", "set", KEYBINDING_SCHEMA, "custom-keybindings", str(paths + [KEYBINDING_PATH]))
    item = f"{KEYBINDING_SCHEMA}.custom-keybinding:{KEYBINDING_PATH}"
    sh("gsettings", "set", item, "name", "Paletti")
    sh("gsettings", "set", item, "command", str(link))
    sh("gsettings", "set", item, "binding", "<Super>y")
    print(f"Installed: {link}, shortcut Super+Y")


def main(args):
    if args == ["--list"]:
        print("\n".join(names()))
    elif args == ["--install"]:
        install()
    elif not args:
        apply(next_name())
    elif len(args) == 1 and args[0] in names():
        apply(args[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
