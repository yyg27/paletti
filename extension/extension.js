// Gives paletti GNOME's own screen transition (the dark style toggle crossfade).
// GNOME keeps org.gnome.Shell.ScreenTransition for itself, so this exports the
// same effect on the session bus with paletti's timing, and reloads the shell
// theme under it:
//   gdbus call --session --dest org.gnome.Shell --object-path /io/paletti/Transition \
//       --method io.paletti.Transition.Reload 350 800
// It also adds a Paletti tile to Quick Settings: click for the next palette,
// open the menu to pick one. Themes are still applied by the paletti script.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Mtk from 'gi://Mtk';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

const IFACE = `<node>
  <interface name="io.paletti.Transition">
    <method name="Reload">
      <arg type="u" name="delay" direction="in"/>
      <arg type="u" name="duration" direction="in"/>
    </method>
  </interface>
</node>`;

// Anything on the session bus can call Reload; cap it so nobody can hold the screen.
const MAX_MS = 3000;

const HOME = GLib.get_home_dir();
const PALETTI = `${HOME}/.local/bin/paletti`; // the Shell's PATH may not have ~/.local/bin
const STATE = `${HOME}/.local/state/paletti/current`;
const ICON = 'color-select-symbolic';

// catppuccin-mocha -> Catppuccin Mocha
const title = name => name.split('-').map(w => w[0].toUpperCase() + w.slice(1)).join(' ');

async function paletti(...args) {
    try {
        const proc = Gio.Subprocess.new([PALETTI, ...args],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
        const [stdout, stderr] = await proc.communicate_utf8_async(null, null);
        if (!proc.get_successful())
            console.error(`paletti ${args.join(' ')}: ${stderr.trim()}`);
        return stdout;
    } catch (e) {
        console.error(`paletti ${args.join(' ')}: ${e.message}`);
        return '';
    }
}

function currentPalette() {
    try {
        const [, bytes] = GLib.file_get_contents(STATE);
        return new TextDecoder().decode(bytes).trim();
    } catch {
        return null;
    }
}

const PalettiToggle = GObject.registerClass(
class PalettiToggle extends QuickSettings.QuickMenuToggle {
    _init() {
        // Not an on/off switch: a click applies the next palette, like Super+Y.
        super._init({title: 'Paletti', iconName: ICON, toggleMode: false});
        this.checked = true; // always drawn as active, so it takes the accent color
        this.menu.setHeader(ICON, 'Paletti');
        this._palettes = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._palettes);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Next wallpaper', () => paletti('--next-wallpaper'));

        this.connect('clicked', () => this._apply());
        // Super+Y switches behind our back, so resync whenever Quick Settings opens.
        Main.panel.statusArea.quickSettings.menu.connectObject('open-state-changed',
            (menu, open) => open && this._sync(), this);
        this.menu.connect('open-state-changed', (menu, open) => open && this._sync());
        this._sync();
    }

    async _apply(...args) {
        await paletti(...args);
        this._sync();
    }

    async _sync() {
        const current = currentPalette();
        this.subtitle = current ? title(current) : null;

        const names = (await paletti('--list')).split('\n').filter(Boolean);
        this._palettes.removeAll();
        for (const name of names) {
            const item = new PopupMenu.PopupMenuItem(title(name));
            item.setOrnament(name === current ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.connect('activate', () => this._apply(name));
            this._palettes.addMenuItem(item);
        }
    }
});

const PalettiIndicator = GObject.registerClass(
class PalettiIndicator extends QuickSettings.SystemIndicator {
    _init() {
        super._init();
        this.quickSettingsItems.push(new PalettiToggle());
    }
});

export default class PalettiExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/io/paletti/Transition');

        this._indicator = new PalettiIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    disable() {
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;

        this._dbus.unexport();
        this._dbus = null;
        this._overlay?.remove_all_transitions(); // onStopped destroys it
        this._overlay = null;
    }

    // Same as GNOME's ScreenTransition.run(): paint the current screen into an
    // overlay now, let paletti switch underneath, then fade the overlay out.
    Reload(delay, duration) {
        this._overlay?.remove_all_transitions(); // a still-fading one; onStopped destroys it

        const rect = new Mtk.Rectangle({x: 0, y: 0, width: global.screen_width, height: global.screen_height});
        const [, , , scale] = global.stage.get_capture_final_size(rect);
        const overlay = new Clutter.Actor({
            content: global.stage.paint_to_content(rect, scale, null, Clutter.PaintFlag.NO_CURSORS),
        });
        overlay.add_constraint(new Clutter.BindConstraint({
            source: Main.uiGroup,
            coordinate: Clutter.BindCoordinate.ALL,
        }));
        Main.uiGroup.add_child(overlay);
        Main.uiGroup.set_child_above_sibling(overlay, null);
        this._overlay = overlay;

        // Once, here: toggling user-theme's name loads the theme ~6 times, about
        // 1s with the Shell blocked, so the fade gets no frames. This one load
        // falls in the delay. user-theme already points at paletti's stylesheet.
        Main.loadTheme();

        overlay.ease({
            opacity: 0,
            delay: Math.min(delay, MAX_MS),
            duration: Math.min(duration, MAX_MS),
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onStopped: () => {
                overlay.destroy();
                if (this._overlay === overlay)
                    this._overlay = null;
            },
        });
    }
}
