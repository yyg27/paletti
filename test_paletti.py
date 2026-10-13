"""Icon recolor checks on fake icon dirs; gsettings is faked. Run: python3 test_paletti.py"""

import os
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
    for accent in ["#bd93f9", "#a7c080"]:
        assert paletti.recolor_icons([], accent) is None
        assert state["icon-theme"] == "Tela-circle-paletti-dark"
        out = icons / "Tela-circle-paletti-dark"
        folder = (out / "scalable/places/folder.svg").resolve().read_bytes()
        assert accent.encode() in folder and b"5294e2" not in folder.lower()
        # System apps take the accent, third-party apps keep their own colors.
        editor = (out / "scalable/apps/org.gnome.TextEditor.svg").resolve().read_bytes().lower()
        assert accent.encode() in editor and b"#32a3d7" not in editor
        blender_out = (out / "scalable/apps/blender.svg").resolve()
        blender_src = (src / "Tela-circle/scalable/apps/blender.svg").resolve().read_bytes()
        assert blender_out.read_bytes() == blender_src  # tint 0: untouched
        assert paletti.recolor_icons([], accent, 0.2) is None
        assert blender_out.read_bytes() != blender_src  # tinted
        assert accent.encode() in (out / "scalable/apps/org.gnome.TextEditor.svg").resolve().read_bytes()
        assert paletti.recolor_icons([], accent, 0.0) is None
        assert blender_out.read_bytes() == blender_src  # tint back to 0 restores it
        # Only links already broken in the Tela package itself may be broken.
        broken = {p.relative_to(icons / p.relative_to(icons).parts[0]) for p in icons.rglob("*")
                  if p.is_symlink() and not p.exists()}
        assert broken <= broken_in_package, broken - broken_in_package


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


def test_adwaita():
    fake_gsettings("Adwaita")
    assert paletti.recolor_icons(["green"], "#a7c080") is None


if __name__ == "__main__":
    for test in [test_variants, test_papirus, test_tela, test_wallpaper, test_interrupted_swap]:
        with tempfile.TemporaryDirectory() as tmp:
            paletti.ICON_DIRS = [Path(tmp)]
            paletti.ICON_THEME_SWAP = Path(tmp) / "icon-theme"
            test(Path(tmp))
    test_adwaita()
    test_window_buttons()
    print("ok")
