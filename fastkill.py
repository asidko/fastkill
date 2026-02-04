#!/usr/bin/env python3
"""Simple process manager using GTK 3."""

import os
import signal
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Pango

EXCLUDED = {
    # Desktop environment
    'xfce4-panel', 'xfce4-session', 'xfce4-notifyd', 'xfwm4', 'xfdesktop',
    'Thunar', 'thunar', 'xfconfd', 'xfsettingsd', 'xfce4-power-manager',
    # System services
    'dbus-daemon', 'dbus-broker', 'at-spi-bus-launcher', 'at-spi2-registryd',
    'gvfsd', 'gvfsd-fuse', 'gvfsd-metadata', 'gvfsd-trash',
    'ibus-daemon', 'ibus-extension-gtk3', 'ibus-portal',
    'polkitd', 'polkit-agent-helper-1', 'polkit-gnome-authentication-agent-1',
    'ssh-agent', 'gpg-agent', 'gnome-keyring-daemon', 'agent',
    'pulseaudio', 'pipewire', 'pipewire-pulse', 'wireplumber',
    'xdg-desktop-portal', 'xdg-document-portal', 'xdg-permission-store',
    'localsearch-extractor-3', 'localsearch-3',
    'copyq', 'Xorg', 'xrdp', 'xrdp-sesman', 'xrdp-chansrv',
    'systemd', 'init', 'login', 'bash', 'zsh', 'sh', 'fish',
    'wrapper-2.0', 'panel-wrapper', 'tumblerd',
    # Sandboxing / containers internals
    'bwrap', 'slirp4netns', 'conmon', 'catatonit', 'aardvark-dns', 'netavark',
    # Notifications / helpers
    'abrt-applet', 'inotifywait', 'gjs', 'gcr-prompter',
    # More system services
    'dbus-broker-launch', 'dbus-launch', 'dconf-service',
    'fusermount3', 'fusermount',
    'ibus-dconf', 'ibus-engine-simple', 'ibus-ui-gtk3',
    'glycin-image-rs', 'glycin-svg', 'glycin-heif',
    'imsettings-daemon', 'nm-applet', 'xfce-polkit', 'xfce4-screensaver',
    'rootlessport', 'rootlessport-child', 'pasta',
    'fortitray', 'fortitraylauncher',
    'Xvfb', 'wsdd', 'dnfdragora-updater',
}

SCRIPT_PID = os.getpid()


class ProcessInfo(NamedTuple):
    pid: int
    name: str  # executable name for grouping
    title: str  # full display title
    description: str


def get_process_details(pid: int) -> str:
    """Get detailed process info for tooltip."""
    proc_path = Path('/proc') / str(pid)
    lines = [f"PID: {pid}"]

    try:
        with open(proc_path / 'cmdline') as f:
            cmdline = f.read().rstrip('\x00').replace('\x00', ' ')
            if len(cmdline) > 200:
                cmdline = cmdline[:200] + "…"
            lines.append(f"Command: {cmdline}")
    except (FileNotFoundError, PermissionError):
        pass

    try:
        with open(proc_path / 'cwd') as f:
            pass
        cwd = os.readlink(proc_path / 'cwd')
        lines.append(f"Working dir: {cwd}")
    except (FileNotFoundError, PermissionError, OSError):
        pass

    try:
        with open(proc_path / 'status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    mem_kb = int(line.split()[1])
                    if mem_kb > 1024:
                        lines.append(f"Memory: {mem_kb // 1024} MB")
                    else:
                        lines.append(f"Memory: {mem_kb} KB")
                    break
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    try:
        with open(proc_path / 'stat') as f:
            stat = f.read().split()
            utime = int(stat[13])
            stime = int(stat[14])
            total_time = (utime + stime) / os.sysconf('SC_CLK_TCK')
            lines.append(f"CPU time: {total_time:.1f}s")
    except (FileNotFoundError, PermissionError, ValueError, IndexError):
        pass

    return '\n'.join(lines)


def get_description(cmdline: list[str]) -> str:
    """Extract description from command line arguments."""
    for arg in cmdline[1:]:
        if arg.startswith('-'):
            continue
        if '/' in arg or arg.endswith(('.py', '.js', '.ts', '.sh', '.rb')):
            parts = Path(arg).parts
            return '/'.join(parts[-3:]) if len(parts) >= 3 else arg
    return ' '.join(cmdline[1:])[:50] if len(cmdline) > 1 else ''


def get_processes() -> list[ProcessInfo]:
    """Get list of user processes, excluding system ones."""
    current_uid = os.getuid()
    processes = []

    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue

        pid = int(entry)
        if pid == SCRIPT_PID:
            continue

        proc_path = Path('/proc') / entry
        try:
            with open(proc_path / 'status') as f:
                status = f.read()
            uid_line = next(l for l in status.splitlines() if l.startswith('Uid:'))
            real_uid = int(uid_line.split()[1])
            if real_uid != current_uid:
                continue

            with open(proc_path / 'cmdline') as f:
                cmdline_raw = f.read()
            if not cmdline_raw:
                continue

            cmdline = cmdline_raw.rstrip('\x00').split('\x00')
            parts = cmdline[0].split(maxsplit=1)
            exe = parts[0]
            name = Path(exe).name
            # Title: name + any inline flags from cmdline[0]
            title = name + (' ' + parts[1] if len(parts) > 1 else '')

            if name in EXCLUDED:
                continue
            if name.startswith(('gvfs', 'xdg-', 'at-spi', 'ibus-', 'glycin-', '(')):
                continue

            description = get_description(cmdline)
            processes.append(ProcessInfo(pid, name, title, description))

        except (FileNotFoundError, PermissionError, StopIteration):
            continue

    return sorted(processes, key=lambda p: p.name.lower())


class GroupHeaderRow(Gtk.ListBoxRow):
    """A header row for a group of processes with the same title."""

    def __init__(self, title: str, count: int, on_toggle: callable = None):
        super().__init__()
        self.title = title
        self.children: list['ProcessRow'] = []
        self._updating = False
        self._on_toggle = on_toggle

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_start(6)
        hbox.set_margin_end(6)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        self.checkbox = Gtk.CheckButton()
        self.checkbox.set_active(True)
        self.checkbox.connect('toggled', self._on_checkbox_toggled)
        hbox.pack_start(self.checkbox, False, False, 0)

        display_title = title[:64] + "…" if len(title) > 64 else title
        title_label = Gtk.Label(xalign=0)
        title_label.set_markup(f"<b>{GLib.markup_escape_text(display_title)}</b> ({count})")
        hbox.pack_start(title_label, True, True, 0)

        self.add(hbox)

    def _on_checkbox_toggled(self, _checkbox) -> None:
        """Handle group checkbox toggle - update all children."""
        if self._updating:
            return
        self._updating = True
        state = self.checkbox.get_active()
        self.checkbox.set_inconsistent(False)
        for child in self.children:
            child.checkbox.set_active(state)
        self._updating = False
        if self._on_toggle:
            self._on_toggle()

    def update_checkbox_state(self) -> None:
        """Update checkbox based on children's states (tri-state logic)."""
        if self._updating or not self.children:
            return
        self._updating = True
        selected = sum(1 for c in self.children if c.checkbox.get_active())
        if selected == 0:
            self.checkbox.set_active(False)
            self.checkbox.set_inconsistent(False)
        elif selected == len(self.children):
            self.checkbox.set_active(True)
            self.checkbox.set_inconsistent(False)
        else:
            self.checkbox.set_active(False)
            self.checkbox.set_inconsistent(True)
        self._updating = False


class ProcessRow(Gtk.ListBoxRow):
    """A row representing a process with checkbox."""

    def __init__(self, proc: ProcessInfo, on_toggle: callable = None, group: GroupHeaderRow = None):
        super().__init__()
        self.proc = proc
        self.group = group

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        indent = 24 if group else 6
        hbox.set_margin_start(indent)
        hbox.set_margin_end(6)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        self.checkbox = Gtk.CheckButton()
        self.checkbox.set_active(True)
        self._on_toggle = on_toggle
        self.checkbox.connect('toggled', self._handle_toggle)
        hbox.pack_start(self.checkbox, False, False, 0)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

        display_title = proc.title[:64] + "…" if len(proc.title) > 64 else proc.title
        title_label = Gtk.Label(label=display_title, xalign=0)
        if not group:
            title_label.set_markup(f"<b>{GLib.markup_escape_text(display_title)}</b>")
        vbox.pack_start(title_label, False, False, 0)

        if proc.description:
            desc_label = Gtk.Label(label=proc.description, xalign=0)
            desc_label.set_ellipsize(Pango.EllipsizeMode.START)
            desc_label.set_opacity(0.6)
            vbox.pack_start(desc_label, False, False, 0)

        hbox.pack_start(vbox, True, True, 0)
        self.add(hbox)

        self.set_tooltip_text(get_process_details(proc.pid))

    def _handle_toggle(self, _checkbox) -> None:
        """Handle checkbox toggle - notify group and callback."""
        if self.group:
            self.group.update_checkbox_state()
        if self._on_toggle:
            self._on_toggle()


class ProcessManager(Gtk.Window):
    """Main window for process management."""

    def __init__(self):
        super().__init__(title="Process Manager")
        self.set_default_size(400, 450)
        self.set_border_width(6)
        self.set_position(Gtk.WindowPosition.CENTER)

        self.kill_mode = 'term'
        self.reset_timer_id = None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(main_box)

        # Top toolbar
        toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar_box.set_margin_bottom(4)

        title_label = Gtk.Label(label="Running Processes", xalign=0)
        title_label.set_markup("<b>Running Processes</b>")
        toolbar_box.pack_start(title_label, True, True, 0)

        refresh_btn = Gtk.Button()
        refresh_icon = Gtk.Image.new_from_icon_name('view-refresh', Gtk.IconSize.SMALL_TOOLBAR)
        refresh_btn.set_image(refresh_icon)
        refresh_btn.set_tooltip_text("Refresh list")
        refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        refresh_btn.connect('clicked', lambda _: self.refresh_list())
        toolbar_box.pack_end(refresh_btn, False, False, 0)

        main_box.pack_start(toolbar_box, False, False, 0)

        # Scrollable list with frame
        frame = Gtk.Frame()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(300)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.listbox)
        frame.add(scrolled)
        main_box.pack_start(frame, True, True, 0)

        # Bottom buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_margin_top(6)

        self.select_all_btn = Gtk.Button(label="Unselect All")
        self.select_all_btn.connect('clicked', self.on_select_all_clicked)
        btn_box.pack_start(self.select_all_btn, False, False, 0)

        self.kill_btn = Gtk.Button(label="Kill Selected")
        self.kill_btn.get_style_context().add_class('destructive-action')
        self.kill_btn.connect('clicked', self.on_kill_clicked)
        btn_box.pack_end(self.kill_btn, False, False, 0)

        main_box.pack_start(btn_box, False, False, 0)

        self.refresh_list()

    def refresh_list(self) -> None:
        """Reload the process list."""
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        processes = get_processes()
        grouped: dict[str, list[ProcessInfo]] = defaultdict(list)
        for proc in processes:
            grouped[proc.name].append(proc)

        for name in sorted(grouped.keys(), key=str.lower):
            procs = grouped[name]
            if len(procs) == 1:
                row = ProcessRow(procs[0], on_toggle=self.update_button_label)
                self.listbox.add(row)
            else:
                group = GroupHeaderRow(name, len(procs), on_toggle=self.update_button_label)
                self.listbox.add(group)
                for proc in procs:
                    row = ProcessRow(proc, on_toggle=self.update_button_label, group=group)
                    group.children.append(row)
                    self.listbox.add(row)

        self.listbox.show_all()
        self.update_button_label()

    def get_selected(self) -> list[ProcessInfo]:
        """Get list of selected processes."""
        selected = []
        for row in self.listbox.get_children():
            if isinstance(row, ProcessRow) and row.checkbox.get_active():
                selected.append(row.proc)
        return selected

    def all_selected(self) -> bool:
        """Check if all processes are selected."""
        process_rows = [r for r in self.listbox.get_children() if isinstance(r, ProcessRow)]
        if not process_rows:
            return False
        return all(row.checkbox.get_active() for row in process_rows)

    def on_select_all_clicked(self, _button) -> None:
        """Toggle selection of all processes."""
        select = not self.all_selected()
        for row in self.listbox.get_children():
            if isinstance(row, ProcessRow):
                row.checkbox.set_active(select)
            elif isinstance(row, GroupHeaderRow):
                row._updating = True
                row.checkbox.set_active(select)
                row.checkbox.set_inconsistent(False)
                row._updating = False
        self.update_button_label()

    def update_button_label(self) -> None:
        """Update button labels based on selection state."""
        count = len(self.get_selected())
        if self.kill_mode == 'term':
            self.kill_btn.set_label(f"Kill Selected ({count})")
        else:
            self.kill_btn.set_label(f"Force Kill ({count})")
        self.select_all_btn.set_label("Unselect All" if self.all_selected() else "Select All")

    def on_kill_clicked(self, _button) -> None:
        """Handle kill button click."""
        selected = self.get_selected()
        if not selected:
            return

        sig = signal.SIGTERM if self.kill_mode == 'term' else signal.SIGKILL

        for proc in selected:
            try:
                os.kill(proc.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass

        if self.kill_mode == 'term':
            self.kill_mode = 'kill'
            self.update_button_label()
            if self.reset_timer_id:
                GLib.source_remove(self.reset_timer_id)
            self.reset_timer_id = GLib.timeout_add_seconds(30, self.reset_kill_mode)

        GLib.timeout_add(500, self.refresh_list)

    def reset_kill_mode(self) -> bool:
        """Reset kill mode back to SIGTERM."""
        self.kill_mode = 'term'
        self.reset_timer_id = None
        self.update_button_label()
        return False


def main() -> None:
    win = ProcessManager()
    win.connect('destroy', Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
