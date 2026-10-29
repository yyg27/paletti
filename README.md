# Paletti

Switch the whole GNOME desktop between color themes with one key.

One palette file drives everything: the top bar, menus, calendar, notifications,
dock, GTK apps, GNOME Terminal, the accent color and the icons. Press
<kbd>Super</kbd>+<kbd>Y</kbd> and the next palette is applied in about a second.

## Supported themes

| Theme | Background | Text | Accent | Accent 2 | GNOME accent |
| --- | --- | --- | --- | --- | --- |
| [Catppuccin](https://catppuccin.com) (mocha) | `#1e1e2e` | `#cdd6f4` | `#cba6f7` | `#f5c2e7` | purple |
| Cyberpunk | `#0d0221` | `#e6f9ff` | `#fcee09` | `#ff003c` | red |
| [Dracula](https://draculatheme.com) | `#282a36` | `#f8f8f2` | `#bd93f9` | `#ff79c6` | purple |
| Evangelion | `#12091e` | `#f0eaf7` | `#88ff5f` | `#a670f5` | green |
| [Everforest](https://github.com/sainnhe/everforest) (dark, medium) | `#2d353b` | `#d3c6aa` | `#a7c080` | `#83c092` | green |
| [Gruvbox](https://github.com/morhetz/gruvbox) (dark) | `#282828` | `#ebdbb2` | `#fabd2f` | `#fe8019` | yellow |
| [Kanagawa](https://github.com/rebelot/kanagawa.nvim) (wave) | `#1f1f28` | `#dcd7ba` | `#7e9cd8` | `#d27e99` | blue |
| Monochrome | `#111111` | `#e6e6e6` | `#e6e6e6` | `#9e9e9e` | slate |
| [Nord](https://www.nordtheme.com) | `#2e3440` | `#eceff4` | `#88c0d0` | `#81a1c1` | blue |
| Redemption | `#141110` | `#e8dcc2` | `#d9181f` | `#f0882a` | red |
| [Tokyo Night](https://github.com/folke/tokyonight.nvim) (night) | `#1a1b26` | `#c0caf5` | `#7aa2f7` | `#bb9af7` | blue |

More themes are one TOML file each, see [Palettes](#palettes).

## What changes

| Part | How |
| --- | --- |
| Top bar, quick settings, calendar, menus, dialogs, OSD, Alt+Tab, notifications | Generates an `Islands` GNOME Shell theme from `shell.css` (island-style top bar) |
| GTK 4 / libadwaita apps | Writes `~/.config/gtk-4.0/gtk.css` from `gtk4.css` |
| GTK 3 apps (with `adw-gtk3`) | Writes `~/.config/gtk-3.0/gtk.css` from `gtk3.css` |
| GNOME accent color | `org.gnome.desktop.interface accent-color` |
| GNOME Terminal | Default profile colors and 16-color palette |
| Dash to Dock | Background color and running-app dots (opacity is left alone) |
| Blur my Shell | Top bar, menu and dock blur turned off; their shapes show around paletti's |
| Window buttons (GTK apps) | Small circles: close in the palette's red, minimize and maximize in its own accent colors |
| Window buttons (Firefox) | The same circles through `chrome/userChrome.css` in the default profile, from `firefox.css`; `user.js` turns on `toolkit.legacyUserProfileCustomizations.stylesheets`. Firefox picks them up on its next start |
| Icons | See [Icons](#icons) |
| Wallpaper and lock screen | See [Wallpapers](#wallpapers) |

## Requirements

- GNOME Shell (built and tested on GNOME 51) with the **User Themes** extension
- Python 3.11+ (standard library only)
- Optional: Dash to Dock, GNOME Terminal, Firefox, `adw-gtk3`, Tela-circle or Papirus icons.
  Anything missing is skipped.

## Install

```sh
git clone <repo> ~/Projects/paletti
~/Projects/paletti/paletti.py --install
```

`--install` links `~/.local/bin/paletti` to the script and adds two custom
shortcuts: <kbd>Super</kbd>+<kbd>Y</kbd> (next palette) and
<kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd> (next wallpaper). Existing
shortcuts are kept.

## Usage

```sh
paletti              # switch to the next palette
paletti everforest   # switch to a specific palette
paletti --list       # list palettes

paletti --next-wallpaper                  # next wallpaper of the current palette
paletti --wallpaper default|chosen|keep   # see Wallpapers
```

The **Paletti** tile in Quick Settings does the same: click it for the next
palette, or open its menu to pick one or change the wallpaper.

A notification shows the new theme. Shell, dock and terminal change instantly;
already open apps pick up new GTK colors when reopened. Files and Terminal keep
running in the background after their last window closes, so quit them first
(`nautilus -q`, or close every terminal window).

## Palettes

A palette is a TOML file in `palettes/`. Adding a theme means adding one file.

```toml
name = "Everforest"
description = "A green based color scheme, designed to be warm and soft."   # notification text
gnome_accent = "green"   # blue teal green yellow orange red pink purple slate
icon_color = ["green"]   # folder color for Papirus / color-variant icon themes
icon_tint = 0.2          # app icons move 20% toward the accent (Tela only)
# icon_grayscale = true  # optional: app icons in grays instead (Tela only, used by Monochrome)
wallpaper = "wallpapers/everforest/everforest.png"   # optional, relative to the project

[colors]
bg            = "#2d353b"
bg_alt        = "#232a2e"
fg            = "#d3c6aa"
surface       = "#475258"
surface_hover = "#56635f"
card          = "#343f44"
card_alt      = "#30393e"
card_hover    = "#3d484d"
accent        = "#a7c080"
accent_hover  = "#b8cd96"
accent2       = "#83c092"
heading       = "#7fbbb3"
muted         = "#859289"

[terminal]
palette = ["#343F44", "#E67E80", ...]   # 16 colors
```

| Role | Used for |
| --- | --- |
| `bg` | Menu and island background, text on accent |
| `bg_alt` | Header bars, sidebars |
| `fg` | Text |
| `surface`, `surface_hover` | Inactive buttons, hover, slider trough |
| `card`, `card_alt`, `card_hover` | Notification cards, submenus |
| `accent`, `accent_hover` | Active buttons, today, switches, dock dots, folders |
| `accent2` | Volume/brightness sliders, weekends |
| `heading` | Calendar day headings |
| `muted` | Selected day, separators |

Every role is required. paletti checks the whole file before switching and names
what is missing or not `#rrggbb` (in a notification, too).

Two optional roles go in `[colors]` too:

| Role | Used for | Default |
| --- | --- | --- |
| `icons` | Icon recoloring (folders, system apps, app icon tint) | `accent` |
| `on_accent` | Text on accent fills (active toggles, today, accent buttons) | `bg` |
Templates use `$role`, and `$role_rgb` (`40, 42, 54`) for `rgba()` values.

## Icons

paletti looks at the active icon theme and does what fits it:

| Icon theme | What happens |
| --- | --- |
| **Tela-circle** (`Tela-circle` or `Tela-circle-dark` selected) | Each accent gets its own copy, `~/.local/share/icons/Tela-circle-paletti-<accent>-t<tint>(-dark)`, built on its first switch (~2 s) and reused after, so later switches are instant. About 33 MB each: unchanged icons are hard links into one shared base copy. Folders take the exact accent color, GNOME system app icons are fully recolored to the accent, other app icons get a light `icon_tint` toward it, or turn gray with `icon_grayscale` (`-gray` copies). Only `tela-circle-icon-theme-standard` needs to be installed. |
| **Papirus** (user-writable copy) | Folder symlinks are pointed at the first `icon_color` Papirus ships. |
| **Color-variant themes** (`Tela-purple-dark`, `Colloid-Green-Dark`, ...) | Switches to the installed variant of the first `icon_color`. |
| **Adwaita** | Nothing to do; folders follow the GNOME accent color. |

Colors are always generated from the original icons, so they never drift. After a
Tela update, delete `~/.local/share/icons/Tela-circle-paletti-*` to rebuild them. Themes that name colors differently (for example `Qogir-manjaro` for green)
are left unchanged with a message.

## Wallpapers

Each palette can name a default wallpaper; it is also used for the lock screen.
The bundled ones live in `wallpapers/`.

Your own wallpapers and the mode go in `~/.config/paletti/config.toml`:

```toml
wallpaper = "default"   # default | chosen | keep

[wallpapers]
dracula = ["~/Pictures/my-dracula.png"]
everforest = ["~/.config/background", "~/Pictures/forest.jpg"]
```

| Mode | Wallpaper on a palette switch |
| --- | --- |
| `default` | The palette's own wallpaper |
| `chosen` | The last one picked with <kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd>, remembered per palette |
| `keep` | Left alone |

<kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>Y</kbd> cycles the palette's default and
your own wallpapers and switches the mode to `chosen`. `paletti --wallpaper`
rewrites the config file, so comments added by hand are not kept.

## Backups

A `gtk.css` paletti did not write is moved to `gtk.css.before-paletti` in
`~/.config/gtk-3.0/` and `~/.config/gtk-4.0/`, the same for Firefox's `userChrome.css`; if that backup already exists, a
timestamp is added to the name, so no backup is ever overwritten. The `Islands` shell theme and the
Tela copy are generated files and are overwritten on every switch.

## Tests

```sh
python3 test_paletti.py
```

Covers icon recoloring on fake icon folders, wallpapers, palette and config
checks, and the `gtk.css` backup. The Tela test runs against the
installed package, or against an extracted one with `TELA_SRC_TEST=/path/to/icons`.

## License

MIT License