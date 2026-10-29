"""Icon recolor checks on fake icon dirs; gsettings is faked. Run: python3 test_paletti.py"""

import os
import re
import tempfile
from pathlib import Path

import paletti


def fake_gsettings(current):
    state = {"icon-theme": current}

    def sh(*cmd):
        if cmd[:2] == ("gsettings", "get"):
            return f"'{state[cmd[-1]]}'"
        if cmd[:2] == ("gsettings", "set"):
            state[cmd[-2]] = cmd[-1]
        return ""
    paletti.sh = sh
    return state


def test_variants(icons):
    for name in ["Tela-purple-dark", "Tela-green-dark", "Colloid-Green-Dark", "Colloid-Purple-Dark"]:
        (icons / name).mkdir()

    state = fake_gsettings("Tela-purple-dark")
    assert paletti.recolor_icons(["green"], "#a7c080") is None
    assert state["icon-theme"] == "Tela-green-dark"
    assert paletti.recolor_icons(["violet", "purple"], "#bd93f9") is None  # no violet, falls back to purple
    assert state["icon-theme"] == "Tela-purple-dark"

    state = fake_gsettings("Colloid-Purple-Dark")
    assert paletti.recolor_icons(["green"], "#a7c080") is None
    assert state["icon-theme"] == "Colloid-Green-Dark"  # keeps the capitalized style

    state = fake_gsettings("Tela-green-dark")
    assert "no red variant" in paletti.recolor_icons(["red"], "#ff5555")
    assert state["icon-theme"] == "Tela-green-dark"


def test_papirus(icons):
    places = icons / "Papirus/48x48/places"
    places.mkdir(parents=True)
    for color in ["violet", "green"]:
        for f in [f"folder-{color}.svg", f"folder-{color}-documents.svg", f"user-{color}-desktop.svg"]:
            (places / f).touch()
        os.symlink(f"user-{color}-desktop.svg", places / f"folder-{color}-desktop.svg")
    os.symlink("folder-violet.svg", places / "folder.svg")
    os.symlink("folder-violet-documents.svg", places / "folder-documents.svg")
    (icons / "Papirus-Dark").mkdir()

    fake_gsettings("Papirus-Dark")
    for color in ["green", "violet", "green"]:
        assert paletti.recolor_icons([color], "#000000") is None
        assert os.readlink(places / "folder.svg") == f"folder-{color}.svg"
        assert os.readlink(places / "folder-documents.svg") == f"folder-{color}-documents.svg"
        # Color-named links never move.
        assert os.readlink(places / "folder-green-desktop.svg") == "user-green-desktop.svg"
        assert os.readlink(places / "folder-violet-desktop.svg") == "user-violet-desktop.svg"


def test_tela(icons):
    """Needs a real Tela package extracted at TELA_SRC_TEST (skipped otherwise)."""
    src = Path(os.environ.get("TELA_SRC_TEST", "/usr/share/icons"))
    if not (src / "Tela-circle").is_dir():
        print("skip test_tela: no Tela-circle at", src)
        return
    paletti.TELA_SRC = src
    broken_in_package = {p.relative_to(src / p.relative_to(src).parts[0]) for v in ["Tela-circle", "Tela-circle-dark"]
                         for p in (src / v).rglob("*") if p.is_symlink() and not p.exists()}
    state = fake_gsettings("Tela-circle-dark")
    blender_src = (src / "Tela-circle/scalable/apps/blender.svg").resolve().read_bytes()
    for accent in ["#bd93f9", "#a7c080"]:
        assert paletti.recolor_icons([], accent) is None
        assert state["icon-theme"] == paletti.tela_name(accent, 0) + "-dark"
        out = icons / state["icon-theme"]
        folder = (out / "scalable/places/folder.svg").resolve().read_bytes()
        assert accent.encode() in folder and b"5294e2" not in folder.lower()
        # System apps take the accent, third-party apps keep their own colors.
        editor = (out / "scalable/apps/org.gnome.TextEditor.svg").resolve().read_bytes().lower()
        assert accent.encode() in editor and b"#32a3d7" not in editor
        assert (out / "scalable/apps/blender.svg").resolve().read_bytes() == blender_src  # tint 0: untouched
        assert paletti.recolor_icons([], accent, 0.2) is None
        tinted = icons / state["icon-theme"]
        assert (tinted / "scalable/apps/blender.svg").resolve().read_bytes() != blender_src
        assert accent.encode() in (tinted / "scalable/apps/org.gnome.TextEditor.svg").resolve().read_bytes()
        assert paletti.recolor_icons([], accent, 0.0) is None
        assert state["icon-theme"] == out.name  # back to the kept one, unchanged
        assert (out / "scalable/apps/blender.svg").resolve().read_bytes() == blender_src
        assert paletti.recolor_icons([], accent, 0.0, True) is None  # grayscale: every app color a gray
        grays = re.findall(rb"#([0-9a-f]{6})", (icons / state["icon-theme"] / "scalable/apps/blender.svg").resolve().read_bytes().lower())
        assert grays and all(g[0:2] == g[2:4] == g[4:6] for g in grays), grays
        assert paletti.recolor_icons([], accent) is None
        # Only links already broken in the Tela package itself may be broken.
        broken = {p.relative_to(icons / p.relative_to(icons).parts[0]) for p in icons.rglob("*")
                  if p.is_symlink() and not p.exists()}
        assert broken <= broken_in_package, broken - broken_in_package
    # Recolored files never leak into the shared base through its hard links.
    base_folder = (icons / "Tela-circle-paletti-base/scalable/places/folder.svg").resolve().read_bytes()
    assert b"5294e2" in base_folder.lower()


def test_wallpaper(tmp):
    own, default = tmp / "own.jpg", paletti.HERE / "wallpapers/dracula/dracula.png"
    own.touch()
    paletti.CONFIG = tmp / "config.toml"
    config = {"wallpaper": "chosen", "wallpapers": {"dracula": [str(own), str(tmp / "missing.jpg")]}}
    paletti.save_config(config)
    assert paletti.load_config() == config  # round trip

    options = paletti.wallpaper_options("dracula", {"wallpaper": "wallpapers/dracula/dracula.png"}, config)
    assert options == [default, own]  # relative to the project, missing file skipped
    assert paletti.pick_wallpaper(options, "default", own) == default
    assert paletti.pick_wallpaper(options, "chosen", own) == own
    assert paletti.pick_wallpaper(options, "chosen", None) == default  # nothing picked yet
    assert paletti.pick_wallpaper(options, "keep", own) is None
    assert paletti.pick_wallpaper([], "default", None) is None


def test_interrupted_swap(icons):
    """A job stopped during set_icon_theme leaves Adwaita set; the next one restores it."""
    (icons / "Tela-purple-dark").mkdir()
    (icons / "Tela-green-dark").mkdir()
    paletti.ICON_THEME_SWAP = icons / "icon-theme"
    paletti.ICON_THEME_SWAP.write_text("Tela-purple-dark")  # what the stopped job left behind
    state = fake_gsettings("Adwaita")
    assert paletti.recolor_icons(["green"], "#a7c080") is None
    assert state["icon-theme"] == "Tela-green-dark"
    assert not paletti.ICON_THEME_SWAP.exists()  # cleaned up after a finished swap

    state = fake_gsettings("Adwaita")  # chosen by the user: no swap file, left alone
    assert paletti.recolor_icons(["green"], "#a7c080") is None
    assert state["icon-theme"] == "Adwaita"


def test_window_buttons():
    """Every palette gets three distinct window button colors."""
    import re
    for name in paletti.names():
        css = paletti.render(paletti.load(name), "gtk4.css")
        colors = re.findall(r"button\.(?:close|minimize|maximize) > image \{ background-color: (#\w+)", css)
        assert len(colors) == 3 and len({c.lower() for c in colors}) == 3, (name, colors)


def test_broken_palette(tmp):
    paletti.sh = lambda *cmd: ""
    paletti.PALETTES = tmp
    (tmp / "broken.toml").write_text('name = "Broken"\n[colors]\nbg = "#fff"\n')
    try:
        paletti.load("broken")
        raise AssertionError("broken palette was accepted")
    except SystemExit as e:
        for problem in ["gnome_accent", "bg_alt", "'#fff'", "16"]:
            assert problem in str(e), (problem, str(e))
    finally:
        paletti.PALETTES = paletti.HERE / "palettes"


def test_gtk_backup(tmp):
    """A gtk.css paletti did not write is moved aside, never overwritten."""
    paletti.sh = lambda *cmd: ""
    paletti.start_icons = lambda name: None
    paletti.THEME_CSS, paletti.STATE, paletti.CONFIG = tmp / "shell.css", tmp / "current", tmp / "config.toml"
    target = tmp / "gtk.css"
    paletti.GTK_CSS = {"gtk4.css": target}
    for own in ["mine 1", "mine 2"]:
        target.write_text(own)
        paletti.apply("dracula")
        assert target.read_text().startswith("/* paletti")
    paletti.apply("dracula")  # its own file is not backed up
    assert sorted(p.read_text() for p in tmp.glob("gtk.css.before-paletti*")) == ["mine 1", "mine 2"]
    assert paletti.STATE.read_text() == "dracula"


def test_firefox(tmp):
    """The default profile gets userChrome.css and the pref, the user's own prefs stay."""
    paletti.sh = lambda *cmd: ""
    paletti.start_icons = lambda name: None
    paletti.THEME_CSS, paletti.STATE, paletti.CONFIG = tmp / "shell.css", tmp / "current", tmp / "config.toml"
    paletti.GTK_CSS = {}
    root = tmp / "firefox"
    for profile in ["abc.default-release", "old.default"]:
        (root / profile).mkdir(parents=True)
    (root / "installs.ini").write_text("[Install4F96D1932A9F858E]\nDefault=abc.default-release\nLocked=1\n")
    user_js = root / "abc.default-release/user.js"
    user_js.write_text('user_pref("browser.startup.page", 3);\n')
    for name in ["dracula", "nord"]:
        paletti.apply(name)
    css = (root / "abc.default-release/chrome/userChrome.css").read_text()
    assert css.startswith("/* paletti-generated (Nord)") and "$" not in css
    assert user_js.read_text().count("legacyUserProfileCustomizations") == 1
    assert user_js.read_text().startswith('user_pref("browser.startup.page", 3);')
    assert not (root / "old.default/chrome").exists()  # not the profile Firefox starts with


def test_broken_config(tmp):
    paletti.sh = lambda *cmd: ""
    paletti.CONFIG = tmp / "config.toml"
    paletti.CONFIG.write_text('wallpaper = "chosen\n')
    assert paletti.load_config() is None
    paletti.apply_wallpaper("dracula", {})  # skipped, no crash
    paletti.STATE = tmp / "current"
    paletti.STATE.write_text("dracula")
    try:
        paletti.next_wallpaper()
        raise AssertionError("broken config was saved over")
    except SystemExit:
        assert paletti.CONFIG.read_text() == 'wallpaper = "chosen\n'


def test_adwaita():
    fake_gsettings("Adwaita")
    assert paletti.recolor_icons(["green"], "#a7c080") is None


if __name__ == "__main__":
    for test in [test_variants, test_papirus, test_tela, test_wallpaper, test_interrupted_swap,
                 test_broken_palette, test_broken_config, test_gtk_backup, test_firefox]:
        with tempfile.TemporaryDirectory() as tmp:
            paletti.ICON_DIRS = [Path(tmp)]
            paletti.FIREFOX_DIRS = [Path(tmp) / "firefox"]  # apply never touches the real profile
            paletti.ICON_THEME_SWAP = Path(tmp) / "icon-theme"
            test(Path(tmp))
    test_adwaita()
    test_window_buttons()
    print("ok")
