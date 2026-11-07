"""Icon recolor checks on fake icon dirs; gsettings is faked. Run: python3 test_paletti.py"""

import base64
import json
import os
import re
import shutil
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


def test_other_apps(icons):
    """Apps Tela has no icon for get the palette's treatment too, from their own SVG or PNG."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    paletti.TELA_SRC = icons / "src"
    (icons / "src/Tela-circle/scalable/apps").mkdir(parents=True)
    (icons / "src/Tela-circle/scalable/apps/known.svg").write_text("")
    data = icons / "data"
    (data / "applications").mkdir(parents=True)
    for app, icon in [("vector", "vector"), ("bitmap", "bitmap"), ("known", "known"), ("pathed", "/opt/x.png")]:
        (data / f"applications/{app}.desktop").write_text(f"[Desktop Entry]\nName={app}\nIcon={icon}\n")
    (data / "icons/hicolor/scalable/apps").mkdir(parents=True)
    (data / "icons/hicolor/scalable/apps/vector.svg").write_text('<svg fill="#ff8800"/>')
    for size in [16, 48]:
        (data / f"icons/hicolor/{size}x{size}/apps").mkdir(parents=True)
        png = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
        png.fill(0xff8800ff)
        png.savev(str(data / f"icons/hicolor/{size}x{size}/apps/bitmap.png"), "png", [], [])
    theme = icons / "Tela-circle-paletti-test"
    dark = icons / "Tela-circle-paletti-test-dark"
    (theme / "scalable/apps").mkdir(parents=True)
    (dark / "scalable").mkdir(parents=True)
    (dark / "scalable/apps").symlink_to("../../Tela-circle-paletti-test/scalable/apps")  # as in Tela
    for variant in [theme, dark]:
        (variant / "index.theme").write_text("[Icon Theme]\nName=test\nDirectories=scalable/apps\n\n"
                                             "[scalable/apps]\nSize=16\nType=Scalable\n")
    saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "XDG_DATA_DIRS")}
    os.environ.update(XDG_DATA_HOME=str(data), XDG_DATA_DIRS=str(icons / "none"))
    try:
        paletti.recolor_other_apps(theme, "#e6e6e6", 0.0, grayscale=True)  # Monochrome
        apps = theme / "scalable/apps"
        assert sorted(p.name for p in apps.iterdir()) == ["bitmap.svg", "vector.svg"]
        assert paletti.gray("ff8800") in (apps / "vector.svg").read_text()
        png = base64.b64decode((apps / "bitmap.svg").read_text().split("base64,")[1].split('"')[0])
        loader = GdkPixbuf.PixbufLoader()
        loader.write(png)
        loader.close()
        pixel = loader.get_pixbuf()
        assert pixel.get_width() == 48  # the biggest PNG was used
        r, g, b = pixel.get_pixels()[:3]
        assert r == g == b == int(paletti.gray("ff8800")[:2], 16)
        if shutil.which("gtk-update-icon-cache"):
            cache = theme / "icon-theme.cache"
            assert cache.exists() and (icons / "Tela-circle-paletti-test-dark/icon-theme.cache").exists()
            before = cache.stat().st_mtime_ns
            paletti.recolor_other_apps(theme, "#e6e6e6", 0.0, grayscale=True)
            assert cache.stat().st_mtime_ns == before  # nothing changed, cache left alone
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})


def test_tela_cleanup(icons):
    """Themes of an old accent go after a switch; a Tela update rebuilds the rest."""
    src = icons / "src"
    for variant in ["", "-dark"]:
        (src / f"Tela-circle{variant}/scalable/places").mkdir(parents=True)
        (src / f"Tela-circle{variant}/index.theme").write_text("[Icon Theme]\nName=Tela circle\n")
        (src / f"Tela-circle{variant}/scalable/places/folder.svg").write_text('<svg fill="#5294e2"/>')
    paletti.TELA_SRC = src
    args = paletti.icon_args(paletti.load(paletti.names()[0]))
    used = icons / paletti.tela_name(*args)
    assert paletti.recolor_tela(*args) is None
    stale = icons / "Tela-circle-paletti-123456-t20-dark"
    stale.mkdir()
    paletti.prune_tela()
    assert not stale.exists() and used.is_dir() and (icons / "Tela-circle-paletti-base").is_dir()
    (used / "marker").write_text("")
    os.utime(src / "Tela-circle/index.theme", ns=(1, 1))  # what a package update does
    assert paletti.recolor_tela(*args) is None
    assert used.is_dir() and not (used / "marker").exists()
    assert (icons / "Tela-circle-paletti-base/paletti-source").read_text() == "1"


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
    (tmp / "broken.toml").write_text('name = "Broken"\nicon_tint = "0.2"\n[colors]\nbg = "#fff"\n')
    try:
        paletti.load("broken")
        raise AssertionError("broken palette was accepted")
    except SystemExit as e:
        for problem in ["gnome_accent", "bg_alt", "'#fff'", "16", "icon_tint must be a number"]:
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


def test_vscode(tmp):
    """A ready-made theme is switched to; a palette without one gets paletti's colors,
    scoped to its theme, which go again on the next switch. The user's settings, even
    the same color keys, stay; a JSONC file is left alone."""
    paletti.sh = lambda *cmd: ""
    paletti.STATE = tmp / "current"  # no previous palette
    code, codium = tmp / "Code/User", tmp / "VSCodium/User"
    code.mkdir(parents=True)
    codium.mkdir(parents=True)
    paletti.VSCODE_DIRS = [code, codium]
    settings = code / "settings.json"
    mine = {"name": "my strings", "scope": "string", "settings": {"foreground": "#123456"}}
    user = {"editor.fontSize": 14,
            "workbench.colorCustomizations": {"minimap.background": "#000000", "editor.background": "#101010"},
            "editor.tokenColorCustomizations": {"textMateRules": [mine]}}
    settings.write_text(json.dumps(user))
    jsonc = '{\n    // mine\n    "editor.fontSize": 14,\n}'
    (codium / "settings.json").write_text(jsonc)

    redemption = paletti.load("redemption")
    for _ in range(2):  # twice: paletti's rules never pile up
        paletti.apply_vscode(redemption)
    result = json.loads(settings.read_text())
    scope = "[Default Dark Modern]"
    colors, tokens = result["workbench.colorCustomizations"], result["editor.tokenColorCustomizations"]
    assert result["workbench.colorTheme"] == "Default Dark Modern" and "$" not in settings.read_text()
    assert colors[scope]["editor.background"] == redemption["colors"]["bg"]
    assert colors["editor.background"] == "#101010" and colors["minimap.background"] == "#000000"
    assert colors[scope]["terminal.ansiBrightWhite"] == redemption["terminal"]["palette"][15]
    assert tokens["textMateRules"] == [mine] and all(r["name"].startswith("paletti:") for r in tokens[scope]["textMateRules"])
    semantic = result["editor.semanticTokenColorCustomizations"][scope]
    assert semantic["enabled"] and semantic["rules"]["function"] == redemption["colors"]["accent2"]

    paletti.apply_vscode(paletti.load("nord"))
    result = json.loads(settings.read_text())
    assert result["workbench.colorTheme"] == "Nord"
    assert {k: v for k, v in result.items() if k != "workbench.colorTheme"} == user
    assert json.loads((code / "settings.json.before-paletti").read_text()) == user
    assert (codium / "settings.json").read_text() == jsonc

    # An older version's unscoped Redemption colors go once; the user's own stay.
    old = json.loads(paletti.render(redemption, "vscode.json"))
    settings.write_text(json.dumps({**user, "workbench.colorCustomizations": {**old["colors"], "minimap.background": "#000000"},
                                    "editor.tokenColorCustomizations": {"textMateRules": old["tokens"] + [mine]},
                                    "editor.semanticTokenColorCustomizations": {"enabled": True, "rules": old["semantic"]}}))
    paletti.STATE.write_text("redemption")
    paletti.apply_vscode(paletti.load("nord"))
    result = json.loads(settings.read_text())
    assert result["workbench.colorCustomizations"] == {"minimap.background": "#000000"}
    assert result["editor.tokenColorCustomizations"] == {"textMateRules": [mine]}
    assert result["editor.semanticTokenColorCustomizations"] == {"enabled": True}


def test_swatches(tmp):
    """Every palette has accent swatches; a broken file gets none instead of breaking the menu."""
    assert all(2 <= len(paletti.swatches(name)) <= 3 for name in paletti.names())
    paletti.PALETTES = tmp
    try:
        (tmp / "broken.toml").write_text("name = ")
        (tmp / "odd.toml").write_text('[colors]\naccent = "red; width: 900px"\naccent2 = "#ABCDEF"\n')
        assert paletti.swatches("broken") == [] and paletti.swatches("odd") == ["#abcdef"]
    finally:
        paletti.PALETTES = paletti.HERE / "palettes"


def test_gdm(tmp):
    """The login screen theme builds from GNOME's own resource and loads; the root
    service accepts only a known palette name from the user's state file."""
    from gi.repository import Gio
    for name in paletti.names():
        assert "$" not in paletti.render(paletti.load(name), "gdm.css")
    # After --gdm-setup the live file is patched; GNOME's own is the saved original.
    source = paletti.GDM_ORIGINAL if paletti.GDM_ORIGINAL.exists() else paletti.GDM_GRESOURCE
    if not source.exists() or paletti.gdm_patched(source):
        print("skip test_gdm build: no unpatched", source)
    else:
        nord, out = paletti.load("nord"), tmp / "theme.gresource"
        paletti.build_gdm_gresource(source, nord, out)
        built, shipped = Gio.Resource.load(str(out)), Gio.Resource.load(str(source))
        theme = "/org/gnome/shell/theme/"
        assert set(built.enumerate_children(theme, 0)) == set(shipped.enumerate_children(theme, 0)) | {"paletti-gdm.png"}
        css = built.lookup_data(theme + "gnome-shell-dark.css", 0).get_data()
        assert css.startswith(shipped.lookup_data(theme + "gnome-shell-dark.css", 0).get_data())
        assert paletti.gdm_patched(out) and nord["colors"]["bg"].encode() in css
        assert paletti.GDM_MARKER not in built.lookup_data(theme + "gnome-shell-high-contrast.css", 0).get_data()

    state = tmp / "current"
    for content, expected in [("nord\n", "nord"), ("../../etc/shadow", None), ("x" * 5000, None)]:
        state.write_text(content)
        assert paletti.read_palette_name(state) == expected
    state.unlink()
    assert paletti.read_palette_name(state) is None
    (tmp / "elsewhere").write_text("nord")
    state.symlink_to(tmp / "elsewhere")
    assert paletti.read_palette_name(state) is None  # a link is never followed as root


def test_gdm_outdated(tmp):
    """The reminder only shows once --gdm-setup ran and its copy no longer matches."""
    home, saved = paletti.GDM_HOME, (paletti.GDM_HOME, paletti.HERE)
    try:
        paletti.GDM_HOME = tmp / "missing"
        assert not paletti.gdm_outdated()  # never set up: no nagging
        paletti.HERE, paletti.GDM_HOME = tmp / "checkout", tmp / "root"
        for root in [paletti.HERE, paletti.GDM_HOME]:
            (root / "palettes").mkdir(parents=True)
            (root / "wallpapers").mkdir()
            (root / "palettes/a.toml").write_text("x")
            (root / "gdm.css").write_text("y")
        assert not paletti.gdm_outdated()
        (paletti.HERE / "palettes/b.toml").write_text("new palette")
        assert paletti.gdm_outdated()
    finally:
        paletti.GDM_HOME, paletti.HERE = saved
    assert paletti.GDM_HOME == home


def test_text_editor(tmp):
    """Every palette's scheme loads in GtkSourceView with the palette's colors, and is chosen by id."""
    import gi
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource
    calls = []
    paletti.sh = lambda *cmd: calls.append(cmd) or ("true" if cmd[1] == "writable" else "")
    paletti.SOURCEVIEW_STYLES = tmp / "styles"
    manager = GtkSource.StyleSchemeManager()
    manager.set_search_path([str(tmp / "styles")])
    for name in paletti.names():
        palette = paletti.load(name)
        paletti.apply_text_editor(name, palette)
        manager.force_rescan()
        scheme = manager.get_scheme(f"paletti-{name}")
        assert scheme is not None, name
        assert scheme.get_style("def:keyword").props.foreground.lower() == palette["colors"]["accent"].lower()
        assert scheme.get_style("text").props.background.lower() == palette["colors"]["bg"].lower()
        assert calls[-1] == ("gsettings", "set", "org.gnome.TextEditor", "style-scheme", f"paletti-{name}")


def test_remind_gdm():
    """The Update button runs --gdm-setup through pkexec; a cancelled password dialog says nothing.
    Each reminder replaces the one before by its saved id."""
    import io
    import subprocess
    run, popen = paletti.subprocess.run, paletti.subprocess.Popen
    with tempfile.TemporaryDirectory() as tmp:
        paletti.GDM_NOTICE = Path(tmp) / "gdm-notice"
        try:
            for clicked, code, expected in [("update", 0, "Login screen updated"), ("update", 126, None),
                                            ("update", 1, "Login screen update failed"), ("", 0, None)]:
                notes, ran = [], []
                replaced = paletti.GDM_NOTICE.exists()

                class Notice:
                    def __init__(self, cmd, **kw):
                        notes.append(cmd[cmd.index("Paletti") + 1:])
                        assert ("-r" in cmd) == replaced and (not replaced or cmd[cmd.index("-r") + 1] == "42")
                        self.stdout = io.StringIO(f"42\n{clicked}\n")

                    def communicate(self):
                        return self.stdout.read(), None
                paletti.subprocess.Popen = Notice
                paletti.sh = lambda *cmd: notes.append(cmd[cmd.index("Paletti") + 1:]) or ""
                paletti.subprocess.run = lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, code, "", "boom")
                paletti.remind_gdm()
                assert notes[0][-2] == "Login screen is out of date" and paletti.GDM_NOTICE.read_text() == "42"
                assert ran == ([] if not clicked else [["pkexec", str(paletti.HOME / ".local/bin/paletti"), "--gdm-setup"]])
                assert (notes[-1][-2] if len(notes) > 1 else None) == expected, (clicked, code, notes)
        finally:
            paletti.subprocess.run, paletti.subprocess.Popen = run, popen


def test_install_vscode_themes():
    """Only missing theme extensions are installed, and only for a VS Code that exists."""
    calls = []

    def sh(*cmd):
        calls.append(cmd)
        return "Dracula-Theme.Theme-Dracula\n" if cmd[1] == "--list-extensions" else "was successfully installed"
    paletti.sh = sh
    which = paletti.shutil.which
    paletti.shutil.which = lambda cli: cli == "code"
    try:
        paletti.install_vscode_themes()
    finally:
        paletti.shutil.which = which
    installed = {cmd[2] for cmd in calls if cmd[1] == "--install-extension"}
    wanted = {paletti.load(n).get("vscode_extension") for n in paletti.names()} - {None}
    assert installed == wanted - {"dracula-theme.theme-dracula"} and installed
    assert all(cmd[0] == "code" for cmd in calls)


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
                 test_broken_palette, test_broken_config, test_gtk_backup, test_firefox, test_tela_cleanup, test_swatches, test_gdm,
                 test_other_apps, test_gdm_outdated, test_text_editor,
                 test_vscode]:
        with tempfile.TemporaryDirectory() as tmp:
            paletti.ICON_DIRS = [Path(tmp)]
            paletti.FIREFOX_DIRS = [Path(tmp) / "firefox"]  # apply never touches the real profile
            paletti.VSCODE_DIRS = [Path(tmp) / "vscode"]  # nor the real VS Code settings
            paletti.SOURCEVIEW_STYLES = Path(tmp) / "styles"  # nor Text Editor's schemes
            paletti.ICON_THEME_SWAP = Path(tmp) / "icon-theme"
            test(Path(tmp))
    test_adwaita()
    test_install_vscode_themes()
    test_remind_gdm()
    test_window_buttons()
    print("ok")
