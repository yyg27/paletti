// Gives paletti GNOME's own screen transition (the dark style toggle crossfade).
// GNOME keeps org.gnome.Shell.ScreenTransition for itself, so this exports the
// same effect on the session bus with paletti's timing:
//   gdbus call --session --dest org.gnome.Shell --object-path /io/paletti/Transition \
//       --method io.paletti.Transition.Freeze 350 800

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Mtk from 'gi://Mtk';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `<node>
  <interface name="io.paletti.Transition">
    <method name="Freeze">
      <arg type="u" name="delay" direction="in"/>
      <arg type="u" name="duration" direction="in"/>
    </method>
  </interface>
</node>`;

// Anything on the session bus can call Freeze; cap it so nobody can hold the screen.
const MAX_MS = 3000;

export default class PalettiExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/io/paletti/Transition');
    }

    disable() {
        this._dbus.unexport();
        this._dbus = null;
        this._overlay?.remove_all_transitions(); // onStopped destroys it
        this._overlay = null;
    }

    // Same as GNOME's ScreenTransition.run(): paint the current screen into an
    // overlay now, let paletti switch underneath, then fade the overlay out.
    Freeze(delay, duration) {
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
