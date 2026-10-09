#!/usr/bin/env python3
"""paletti: switch the GNOME desktop color theme with one command.

    paletti              switch to the next theme
    paletti everforest   switch to a specific theme
    paletti --list       list themes
    paletti --install    ~/.local/bin/paletti symlink + Super+Y shortcut
"""

import ast
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
