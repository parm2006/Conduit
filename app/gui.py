import customtkinter as ctk
import logging
import socket
import threading
from pathlib import Path
from tkinter import messagebox

from app.server import ConduitServer
from app.client import ConduitClient
from app.file_transfer.toast import TransferToast
from app.crypto import certificate_fingerprint, pairing_code_from_fingerprint
from app.pairing_dialog import PairingApprovalController
from app.safe_errors import error_name, public_error_message
from app.preferences import UserPreferences
from app.ports import DEFAULT_BASE_PORT
from app.version import PRODUCT_NAME, PRODUCT_VERSION
from app.global_hotkey import GlobalHotkeyMonitor
from app.firewall import FirewallInspection, FirewallRuleSpec, FirewallState
from app.firewall_onboarding import (
    FirewallOnboarding,
    FirewallSetupOutcome,
    firewall_display,
)
from app.display_topology import DraftTopology, PlacedMachine
from app.topology_editor import CLIENT_COLORS, TopologyEditor
from app.topology_toast import (
    DisplayChangeWarningToast,
    TopologyIdentificationToast,
)
from app.windows_displays import (
    DisplayChangeMonitor,
    WindowsDisplayDiscovery,
    display_group_from_message,
)
from app.machine_identity import windows_machine_id
from app.session import CandidateDecision, PENDING_CLIENT_COLOR

logger = logging.getLogger(__name__)

def configure_main_window(window):
    window.title(f"{PRODUCT_NAME} {PRODUCT_VERSION}")
    window.geometry("400x650")
    window.resizable(False, False)


def restore_saved_role(tabview, role):
    labels = {"server": "Server (Host)", "client": "Client"}
    label = labels.get(role)
    if label is not None:
        tabview.set(label)


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65533 else None


def save_role_safely(preferences, role):
    try:
        preferences.save_role(role)
        return True
    except Exception as error:
        logger.error("Could not save successful Conduit role (%s)", error_name(error))
        return False


import socket


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def write_status_message(widget, message, color="gray", white_text=None, show_ip=False):
    widget.configure(state="normal", text_color=color)
    widget.delete("1.0", "end")
    if show_ip:
        ip = get_local_ip()
        widget.insert("end", f"Server IP (IPv4): {ip}\n")
    if white_text and white_text in message:
        before, after = message.split(white_text, 1)
        widget.insert("end", before)
        widget.tag_config("pairing_code", foreground="white")
        widget.insert("end", white_text, "pairing_code")
        widget.insert("end", after)
    else:
        widget.insert("end", message)
    widget.configure(state="disabled")


def _firewall_scope_text(spec):
    message = (
        "Allow Conduit Server on private local networks?\n\n"
        "Windows will allow this Conduit executable to receive TCP "
        f"connections on ports {spec.local_ports} from devices on your local "
        "network. Public networks remain blocked."
    )
    if spec.development_scope:
        message += (
            "\n\nThis development build runs through Python. Windows can "
            "restrict the rule to this Python executable, but not to the "
            "Conduit script alone. Packaged releases use a Conduit-specific "
            "rule."
        )
    return message


def _firewall_conflict_text(spec):
    message = (
        "Windows is blocking incoming Conduit connections.\n\n"
        "Repair will disable only the conflicting firewall rule for this "
        "exact executable, then verify Conduit's restricted rule.\n\n"
        f"Executable: {spec.executable_path}\n"
        f"TCP ports: {spec.local_ports}\n"
        "Scope: Private networks and LocalSubnet only. Public networks remain "
        "blocked."
    )
    if spec.development_scope:
        message += (
            "\n\nThis development build runs through Python. Disabling the "
            "conflicting rule affects this Python executable, which other "
            "Python applications may share. Packaged releases use a "
            "Conduit-specific executable."
        )
    return message


def ask_firewall_start_choice(
    parent,
    spec,
    *,
    configure_allowed=True,
    repair_required=False,
):
    """Show the explicit three-way Server start decision."""
    dialog = ctk.CTkToplevel(parent)
    dialog.title("Conduit Firewall")
    tall_dialog = spec.development_scope or repair_required
    dialog.geometry("480x390" if tall_dialog else "430x270")
    dialog.resizable(False, False)
    dialog.transient(parent)
    result = {"value": "cancel"}

    ctk.CTkLabel(
        dialog,
        text=(
            _firewall_conflict_text(spec)
            if repair_required
            else _firewall_scope_text(spec)
        ),
        wraplength=440 if repair_required else 390,
        justify="left",
    ).pack(fill="x", padx=20, pady=(20, 14))

    def choose(value):
        result["value"] = value
        dialog.destroy()

    if repair_required:
        ctk.CTkButton(
            dialog,
            text="Repair and start",
            command=lambda: choose("repair"),
        ).pack(fill="x", padx=40, pady=4)
    elif configure_allowed:
        ctk.CTkButton(
            dialog,
            text="Configure and start",
            command=lambda: choose("configure"),
        ).pack(fill="x", padx=40, pady=4)
    if not repair_required:
        ctk.CTkButton(
            dialog,
            text="Start without setup",
            command=lambda: choose("without_setup"),
        ).pack(fill="x", padx=40, pady=4)
    ctk.CTkButton(
        dialog,
        text="Cancel",
        fg_color="gray",
        command=lambda: choose("cancel"),
    ).pack(fill="x", padx=40, pady=4)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
    dialog.grab_set()
    dialog.wait_window()
    return result["value"]


import tkinter


class EntryUndoManager:
    def __init__(self, target_entry):
        self.target = target_entry
        try:
            self.history = [target_entry.get()]
        except Exception:
            self.history = [""]
        self.index = 0
        self._ignore = False

    def on_change(self, event=None):
        if self._ignore:
            return
        try:
            val = self.target.get()
        except Exception:
            return
        if self.history and self.history[self.index] == val:
            return
        self.history = self.history[:self.index + 1]
        self.history.append(val)
        self.index = len(self.history) - 1

    def undo(self, event=None):
        if self.index > 0:
            self.index -= 1
            self._apply()
        return "break"

    def redo(self, event=None):
        if self.index < len(self.history) - 1:
            self.index += 1
            self._apply()
        return "break"

    def _apply(self):
        self._ignore = True
        try:
            self.target.delete(0, 'end')
            self.target.insert(0, self.history[self.index])
        except Exception:
            pass
        finally:
            self._ignore = False


def enable_textbox_qol(widget):
    inner_entry = getattr(widget, '_entry', widget)
    if not hasattr(inner_entry, 'bind'):
        return None

    undo_mgr = EntryUndoManager(inner_entry)

    def _on_key_release(event):
        if event.keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R"):
            return
        undo_mgr.on_change(event)

    def _select_all(event):
        try:
            inner_entry.select_range(0, 'end')
            inner_entry.icursor('end')
        except Exception:
            pass
        return "break"

    def _delete_word_left(event):
        try:
            cursor_pos = inner_entry.index('insert')
            text = inner_entry.get()
            if cursor_pos > 0:
                left_text = text[:cursor_pos].rstrip()
                space_idx = left_text.rfind(' ')
                new_pos = space_idx + 1 if space_idx != -1 else 0
                inner_entry.delete(new_pos, cursor_pos)
                undo_mgr.on_change()
        except Exception:
            pass
        return "break"

    def _delete_word_right(event):
        try:
            cursor_pos = inner_entry.index('insert')
            text = inner_entry.get()
            if cursor_pos < len(text):
                right_text = text[cursor_pos:].lstrip()
                space_idx = text.find(' ', cursor_pos + (len(text[cursor_pos:]) - len(right_text)))
                new_pos = space_idx if space_idx != -1 else len(text)
                inner_entry.delete(cursor_pos, new_pos)
                undo_mgr.on_change()
        except Exception:
            pass
        return "break"

    def _show_context_menu(event):
        menu = tkinter.Menu(inner_entry, tearoff=0)

        def _cut():
            try:
                inner_entry.event_generate("<<Cut>>")
                undo_mgr.on_change()
            except Exception:
                pass

        def _copy():
            try:
                inner_entry.event_generate("<<Copy>>")
            except Exception:
                pass

        def _paste():
            try:
                inner_entry.event_generate("<<Paste>>")
                undo_mgr.on_change()
            except Exception:
                pass

        def _select_all_menu():
            try:
                inner_entry.select_range(0, 'end')
                inner_entry.icursor('end')
            except Exception:
                pass

        menu.add_command(label="Undo", command=undo_mgr.undo)
        menu.add_command(label="Redo", command=undo_mgr.redo)
        menu.add_separator()
        menu.add_command(label="Cut", command=_cut)
        menu.add_command(label="Copy", command=_copy)
        menu.add_command(label="Paste", command=_paste)
        menu.add_separator()
        menu.add_command(label="Select All", command=_select_all_menu)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    try:
        inner_entry.bind("<KeyRelease>", _on_key_release, add="+")
        inner_entry.bind("<Control-a>", _select_all, add="+")
        inner_entry.bind("<Control-A>", _select_all, add="+")
        inner_entry.bind("<Control-z>", undo_mgr.undo, add="+")
        inner_entry.bind("<Control-Z>", undo_mgr.redo, add="+")
        inner_entry.bind("<Control-y>", undo_mgr.redo, add="+")
        inner_entry.bind("<Control-Y>", undo_mgr.redo, add="+")
        inner_entry.bind("<Control-BackSpace>", _delete_word_left, add="+")
        inner_entry.bind("<Control-Delete>", _delete_word_right, add="+")
        inner_entry.bind("<Button-3>", _show_context_menu, add="+")
        inner_entry.bind("<Button-2>", _show_context_menu, add="+")
    except Exception as error:
        logger.debug("Could not attach textbox QoL bindings: %s", error_name(error))

    return undo_mgr


class ConduitGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        icon_path = Path(__file__).parent / "assets" / "app_icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass
        configure_main_window(self)
        
        self.server = None
        self.client = None
        self.preferences = UserPreferences()
        self.firewall_onboarding = FirewallOnboarding(
            scheduler=lambda callback: self.after(0, callback),
        )
        self._firewall_refresh_token = None
        self._firewall_start_warning = None
        saved_role = self.preferences.load_role()
        self.legacy_layout_position = self.preferences.load_client_position()
        self.known_hosts = self.load_known_hosts()
        self.overlay_center_x = self.winfo_screenwidth() // 2
        self.overlay_center_y = self.winfo_screenheight() // 2
        self.overlay = None
        self.overlay_active = False
        self._is_reloading = False
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self._close_started = False
        self._intentional_disconnect_session_ids = set()
        self._expected_server_stop = False
        self._server_stopping = False
        self.transfer_toast = TransferToast(self, self._cancel_transfer)
        self.topology_toast = TopologyIdentificationToast(
            self,
            on_disconnect=self.disconnect_client,
        )
        self.display_warning_toast = DisplayChangeWarningToast(self)
        self._server_display_monitor = None
        self.pairing_approval = PairingApprovalController(
            self,
            on_status=lambda message: self._set_status(message, "orange"),
        )
        
        # UI setup
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # Tabs for Mode Selection
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        
        self.tab_server = self.tabview.add("Server (Host)")
        self.tab_client = self.tabview.add("Client")
        restore_saved_role(self.tabview, saved_role)

        # A machine may switch roles while testing or reconnecting. Keep one
        # in-memory password across both tabs so the hidden fields cannot drift
        # into two different credentials. The value is intentionally not
        # persisted by UserPreferences.
        self.shared_password = tkinter.StringVar(master=self)
        
        # Server UI
        saved_server_port = str(self.preferences.load_server_port())
        self.server_port_label = ctk.CTkLabel(self.tab_server, text="Port:")
        self.server_port_label.pack(pady=5)
        self.server_port_entry = ctk.CTkEntry(self.tab_server)
        self.server_port_entry.insert(0, saved_server_port)
        self.server_port_entry.pack(pady=5)
        enable_textbox_qol(self.server_port_entry)
        self.server_port_entry.bind(
            "<KeyRelease>",
            lambda event: self._schedule_firewall_refresh(),
            add="+",
        )

        self.firewall_row = ctk.CTkFrame(
            self.tab_server,
            fg_color="transparent",
        )
        self.firewall_row.pack(fill="x", padx=12, pady=(0, 2))
        self.firewall_status_label = ctk.CTkLabel(
            self.firewall_row,
            text="Firewall: Checking...",
            anchor="w",
        )
        self.firewall_status_label.pack(side="left", expand=True, fill="x")
        self.firewall_action_btn = ctk.CTkButton(
            self.firewall_row,
            text="Configure",
            width=82,
            height=28,
            command=self._on_firewall_action,
        )
        
        self.server_password_label = ctk.CTkLabel(self.tab_server, text="Password:")
        self.server_password_label.pack(pady=2)
        self.server_password_entry = ctk.CTkEntry(
            self.tab_server,
            show="*",
            textvariable=self.shared_password,
        )
        self.server_password_entry.pack(pady=2)
        enable_textbox_qol(self.server_password_entry)
        
        saved_topology = self.preferences.load_active_topology()
        windows_name = socket.gethostname()
        server_machine_id = (
            saved_topology.server_id
            if saved_topology is not None
            else windows_machine_id()
        )
        try:
            server_group = WindowsDisplayDiscovery().discover(
                server_machine_id,
                windows_name,
            )
        except Exception as error:
            logger.error("Could not discover Server displays (%s)", error_name(error))
            if saved_topology is None:
                raise
            server_group = next(
                placed.group
                for placed in saved_topology.machines
                if placed.group.machine_id == saved_topology.server_id
            )
        if saved_topology is None:
            saved_topology = DraftTopology(
                server_id=server_machine_id,
                machines=(PlacedMachine(server_group, 0, 0),),
            ).validate().validated.activate(version=0)
        current_draft = DraftTopology(
            server_id=server_machine_id,
            machines=(PlacedMachine(server_group, 0, 0),),
        )
        self.topology_editor = TopologyEditor(
            self.tab_server,
            active=saved_topology,
            draft=current_draft,
            on_apply=self._on_topology_apply,
            on_cancel=self._on_topology_cancel,
            on_rescan=self._begin_topology_rescan,
        )
        self.topology_editor.pack(pady=8)
        
        self.server_start_btn = ctk.CTkButton(self.tab_server, text="Start Server", command=self.start_server)
        self.server_start_btn.pack(pady=10)
        self.server_stop_btn = ctk.CTkButton(self.tab_server, text="Stop Server", fg_color="red", hover_color="darkred", command=self.stop_server)
        
        # Client UI
        self.client_ip_label = ctk.CTkLabel(self.tab_client, text="Server IP:")
        self.client_ip_label.pack(pady=5)
        
        default_ip = self.known_hosts[0]['ip'] if self.known_hosts else "127.0.0.1"
        ip_list = [h['ip'] for h in self.known_hosts] if self.known_hosts else ["127.0.0.1"]
        
        self.client_ip_entry = ctk.CTkComboBox(self.tab_client, values=ip_list, command=self.on_ip_select)
        self.client_ip_entry.set(default_ip)
        self.client_ip_entry.pack(pady=5)
        enable_textbox_qol(self.client_ip_entry)
        
        self.client_port_label = ctk.CTkLabel(self.tab_client, text="Port:")
        self.client_port_label.pack(pady=5)
        self.client_port_entry = ctk.CTkEntry(self.tab_client)
        default_port = (
            str(self.known_hosts[0]['port'])
            if self.known_hosts
            else str(DEFAULT_BASE_PORT)
        )
        self.client_port_entry.insert(0, default_port)
        self.client_port_entry.pack(pady=5)
        enable_textbox_qol(self.client_port_entry)
        
        self.client_password_label = ctk.CTkLabel(self.tab_client, text="Password:")
        self.client_password_label.pack(pady=5)
        self.client_password_entry = ctk.CTkEntry(
            self.tab_client,
            show="*",
            textvariable=self.shared_password,
        )
        self.client_password_entry.pack(pady=5)
        enable_textbox_qol(self.client_password_entry)
        
        self.client_connect_btn = ctk.CTkButton(self.tab_client, text="Connect", command=self.connect_client)
        self.client_connect_btn.pack(pady=10)
        self.client_disconnect_btn = ctk.CTkButton(self.tab_client, text="Disconnect", fg_color="red", hover_color="darkred", command=self.disconnect_client)

        self.repair_btn = ctk.CTkButton(
            self.tab_client, text="Forget saved identity and re-pair",
            command=self.clear_client_trust,
        )
        self.repair_btn.pack(pady=5)

        self.status_text = ctk.CTkTextbox(self, height=92, wrap="word")
        self.status_text.grid(row=1, column=0, padx=20, pady=(0, 14), sticky="nsew")
        self._set_status("Status: Idle", "gray")
        
        # Global Hotkey Monitor
        self.global_hotkey_monitor = GlobalHotkeyMonitor(
            on_emergency_exit=self._on_emergency_exit_global,
            on_reload_connection=self._on_reload_connection_global,
            on_toggle_daemon=self.toggle_daemon_mode,
        )
        self.global_hotkey_monitor.start()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(0, self._refresh_firewall_status)

    def load_known_hosts(self):
        try:
            return self.preferences.load_successful_hosts()
        except Exception as error:
            logger.error("Failed to load known hosts (%s)", error_name(error))
        return []

    def save_known_host(self, ip, port):
        try:
            self.preferences.save_successful_host(ip, port)
            self.known_hosts = self.preferences.load_successful_hosts()
            self.client_ip_entry.configure(values=[h['ip'] for h in self.known_hosts])
            self.client_ip_entry.set(self.known_hosts[0]['ip'])
            self.client_port_entry.delete(0, 'end')
            self.client_port_entry.insert(0, str(self.known_hosts[0]['port']))
        except Exception as error:
            logger.error("Failed to save known host (%s)", error_name(error))

    def on_ip_select(self, choice):
        for host in self.known_hosts:
            if host['ip'] == choice:
                self.client_port_entry.delete(0, 'end')
                self.client_port_entry.insert(0, str(host['port']))
                break

    def _schedule_firewall_refresh(self):
        token = self._firewall_refresh_token
        if token is not None:
            try:
                self.after_cancel(token)
            except Exception:
                pass
        self._firewall_refresh_token = self.after(
            250,
            self._refresh_firewall_status,
        )

    def _refresh_firewall_status(self):
        self._firewall_refresh_token = None
        port = parse_port(self.server_port_entry.get())
        if port is None:
            inspection = FirewallInspection(
                FirewallState.UNAVAILABLE,
                "invalid_port",
            )
        else:
            self.firewall_onboarding.refresh(port)
            inspection = self.firewall_onboarding.inspection
        self._render_firewall_inspection(inspection)

    def _render_firewall_inspection(self, inspection):
        display = firewall_display(inspection)
        self.firewall_status_label.configure(
            text=f"Firewall: {display.label}",
            text_color=display.color,
        )
        if display.action is None:
            self.firewall_action_btn.pack_forget()
        else:
            self.firewall_action_btn.configure(
                text=display.action,
                state="normal",
            )
            self.firewall_action_btn.pack(side="right")

    def _show_firewall_help(self):
        inspection = self.firewall_onboarding.inspection
        display = firewall_display(inspection)
        messagebox.showinfo("Conduit Firewall", display.explanation)

    def _on_firewall_action(self):
        port = parse_port(self.server_port_entry.get())
        if port is None:
            self._set_status(
                "Status: Invalid port\n"
                "Enter a base port from 1 to 65533.",
                "red",
            )
            return
        if self.firewall_onboarding.inspection.state not in {
            FirewallState.MISSING,
            FirewallState.STALE,
            FirewallState.CONFLICT,
        }:
            self._show_firewall_help()
            return
        spec = FirewallRuleSpec(self.firewall_onboarding.executable_path, port)
        conflict = (
            self.firewall_onboarding.inspection.state
            is FirewallState.CONFLICT
        )
        consent = messagebox.askyesno(
            "Repair Conduit Firewall"
            if conflict
            else "Configure Conduit Firewall",
            _firewall_conflict_text(spec)
            if conflict
            else _firewall_scope_text(spec),
        )
        def complete_setup(result):
            self._render_firewall_inspection(
                self.firewall_onboarding.inspection
            )
            if result.outcome is FirewallSetupOutcome.DECLINED:
                self._set_status(
                    "Status: Firewall setup was cancelled.",
                    "orange",
                )
            elif result.outcome is not FirewallSetupOutcome.READY:
                self._set_status(
                    "Status: Firewall setup did not complete.\n"
                    "Try again or ask your administrator for help.",
                    "red",
                )

        result = self.firewall_onboarding.configure_async(
            port,
            consent=lambda scope: consent,
            on_complete=complete_setup,
        )
        if result is None:
            self.firewall_action_btn.configure(state="disabled")
            self._set_status(
                "Status: Repairing Windows Firewall..."
                if conflict
                else "Status: Configuring Windows Firewall...",
                "orange",
            )
        else:
            complete_setup(result)

    def start_server(self):
        port = parse_port(self.server_port_entry.get())
        if port is None:
            self._set_status(
                "Status: Invalid port\nEnter a base port from 1 to 65533.",
                "red",
            )
            return
        password = self.server_password_entry.get()
        
        if not password:
            self._set_status("Status: Error - Password required", "red")
            return

        onboarding = self.__dict__.get("firewall_onboarding")
        if onboarding is None:
            self._start_server_after_firewall(port, password)
            return
        if onboarding.busy:
            return

        onboarding.refresh(port)
        if hasattr(self, "_render_firewall_inspection"):
            self._render_firewall_inspection(onboarding.inspection)
        if onboarding.inspection.state in {
            FirewallState.READY,
            FirewallState.DEVELOPMENT,
        }:
            self._firewall_start_warning = (
                "Development firewall rule targets Python."
                if onboarding.inspection.state is FirewallState.DEVELOPMENT
                else None
            )
            self._start_server_after_firewall(port, password)
            return

        spec = FirewallRuleSpec(onboarding.executable_path, port)
        configure_allowed = onboarding.inspection.state in {
            FirewallState.MISSING,
            FirewallState.STALE,
        }
        repair_required = (
            onboarding.inspection.state is FirewallState.CONFLICT
        )
        choice = ask_firewall_start_choice(
            self,
            spec,
            configure_allowed=configure_allowed,
            repair_required=repair_required,
        )
        if choice in {"configure", "repair"}:
            self._firewall_start_warning = None

            def start_after_setup():
                if (
                    onboarding.inspection.state
                    is FirewallState.DEVELOPMENT
                ):
                    self._firewall_start_warning = (
                        "Development firewall rule targets Python."
                    )
                self._start_server_after_firewall(port, password)

            def complete_setup(result):
                self.server_start_btn.configure(state="normal")
                self._render_firewall_inspection(onboarding.inspection)
                if result.outcome is FirewallSetupOutcome.DECLINED:
                    self._set_status(
                        "Status: Firewall setup was cancelled. Server not started.",
                        "orange",
                    )
                elif result.outcome is not FirewallSetupOutcome.READY:
                    self._set_status(
                        "Status: Firewall setup did not complete. Server not "
                        "started.\nTry again or ask your administrator for help.",
                        "red",
                    )

            result = onboarding.configure_async(
                port,
                consent=lambda scope: True,
                on_ready=start_after_setup,
                on_complete=complete_setup,
            )
            if result is None:
                self.server_start_btn.configure(state="disabled")
                self._set_status(
                    "Status: Repairing Windows Firewall..."
                    if choice == "repair"
                    else "Status: Configuring Windows Firewall...",
                    "orange",
                )
            else:
                complete_setup(result)
        elif choice == "without_setup":
            self._firewall_start_warning = (
                "Firewall setup was skipped; remote connections may be blocked."
            )
            self._start_server_after_firewall(port, password)

    def _start_server_after_firewall(self, port, password):
        if self.server:
            self._stop_server_display_monitor()
            self.server.stop()
            
        if not self.overlay:
            self._init_overlay()
            
        self.server = ConduitServer(
            password=password, 
            port=port, 
            on_capture_start=self.show_overlay, 
            on_capture_stop=self.hide_overlay,
            on_transfer_status=self._on_transfer_status,
            on_app_shutdown=self._on_emergency_exit_global,
            on_topology_edit_cancel=lambda: self.after(
                0,
                self.topology_editor.cancel_edits,
            ),
        )
        self.server.control_network.register_callback('connected', self._on_server_client_connected)
        self.server.control_network.register_callback('disconnected', self._on_server_client_disconnected)
        self.server.control_network.register_callback('set_daemon_mode', self._on_remote_daemon_mode)
        self.server.control_network.register_callback('disconnect_notice', self._on_disconnect_notice)
        self.server.control_network.register_callback('reload_connection', self._on_remote_reload_connection)
        self.server.control_network.register_callback('shutdown_app', self._on_remote_app_shutdown)
        self.server.control_network.register_callback(
            'candidate_pending',
            lambda data, source=self.server: self._on_server_candidate_pending(
                source,
                data,
            ),
        )
        self.server.control_network.register_callback(
            'display_inventory',
            lambda data, source=self.server: self._on_server_display_inventory(
                source,
                data,
            ),
        )
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.server.set_screen_size(screen_width, screen_height)
        
        if self.server.start():
            activate_topology = getattr(
                self.server,
                'activate_client_topology',
                None,
            )
            if activate_topology is not None:
                activate_topology(self.topology_editor.state.active)
            save_role_safely(self.preferences, "server")
            try:
                self.preferences.save_server_port(port)
            except Exception as error:
                logger.error("Could not save server port (%s)", error_name(error))
            fingerprint = certificate_fingerprint(self.server.identity.cert_path)
            code = pairing_code_from_fingerprint(fingerprint)
            recovery = (
                "\nA damaged identity was replaced; existing clients must re-pair."
                if self.server.identity.recovered else ""
            )
            self._set_status(
                f"Status: Server listening on port {port}\n"
                f"Pairing code: {code}{recovery}"
                + (
                    f"\nWarning: {self._firewall_start_warning}"
                    if self.__dict__.get("_firewall_start_warning")
                    else ""
                ),
                (
                    "orange"
                    if self.server.identity.recovered
                    or self.__dict__.get("_firewall_start_warning")
                    else "green"
                ),
                white_text=code,
            )
            self.server_start_btn.pack_forget()
            self.server_stop_btn.pack(pady=10)
            self._start_server_display_monitor(self.server)
        else:
            self._set_status(
                "Status: Could not start server\n"
                "Check whether the selected port is already in use.",
                "red",
            )

    def connect_client(self):
        ip = self.client_ip_entry.get()
        port = parse_port(self.client_port_entry.get())
        if port is None:
            self._set_status(
                "Status: Invalid port\nEnter a base port from 1 to 65533.",
                "red",
            )
            return
        password = self.client_password_entry.get()
        
        if not password:
            self._set_status("Status: Error - Password required", "red")
            return
        
        if self.client:
            self.client.disconnect()
            
        client = ConduitClient(
            password=password,
            on_transfer_status=self._on_transfer_status,
            fingerprint_approval=self._approve_fingerprint,
            on_app_shutdown=self._on_emergency_exit_global,
        )
        self.client = client
        client.on_reload_callback = lambda: self.after(0, self.reconnect_client)
        client.control_network.register_callback(
            'disconnected',
            lambda data, source=client: self._on_client_disconnected_event(source, data),
        )
        client.control_network.register_callback('set_daemon_mode', self._on_remote_daemon_mode)
        client.control_network.register_callback('disconnect_notice', self._on_disconnect_notice)
        client.control_network.register_callback('reload_connection', self._on_remote_reload_connection)
        client.control_network.register_callback('shutdown_app', self._on_remote_app_shutdown)
        client.control_network.register_callback(
            'topology_identify',
            lambda data, source=client: self._on_topology_identify(source, data),
        )
        client.control_network.register_callback(
            'candidate_pending',
            lambda data, source=client: self._on_candidate_pending_toast(
                source,
                data,
            ),
        )
        client.control_network.register_callback(
            'candidate_closed',
            lambda data, source=client: self._hide_topology_toast(source),
        )
        client.control_network.register_callback(
            'topology_applied',
            lambda data, source=client: self._hide_topology_toast(source),
        )
        client.control_network.register_callback(
            'topology_cancelled',
            lambda data, source=client: self._hide_topology_toast(source),
        )
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        client.set_screen_size(screen_width, screen_height)
        
        self._set_status(f"Status: Connecting to {ip}:{port}...", "orange")
        self.client_connect_btn.configure(state="disabled")
        
        def _on_connect_result(success, error_msg):
            # This is called from a background thread, use after() to update GUI safely
            self.after(
                0,
                lambda: self._handle_connect_result(
                    client, success, error_msg, ip, port
                ),
            )
            
        client.connect(ip, port, _on_connect_result)

    def _handle_connect_result(self, source, success, error_msg, ip, port):
        if self.client is not source:
            return
        self.client_connect_btn.configure(state="normal")
        self._is_reloading = False
        if success:
            save_role_safely(self.preferences, "client")
            self._set_status(f"Status: Connected to {ip}:{port}", "green")
            self.save_known_host(ip, port)
            self.client_connect_btn.pack_forget()
            self.client_disconnect_btn.pack(pady=10)
        else:
            toast = self.__dict__.get("topology_toast")
            if toast is not None:
                toast.hide()
            self._set_status(f"Status: Connection failed\n{error_msg}", "red")

    def _start_server_display_monitor(self, source):
        self._stop_server_display_monitor()
        editor = self.__dict__.get("topology_editor")
        if editor is None:
            return False
        server_id = editor.state.draft.server_id
        initial_group = next(
            placed.group
            for placed in editor.state.draft.machines
            if placed.group.machine_id == server_id
        )
        monitor = DisplayChangeMonitor(
            WindowsDisplayDiscovery(),
            server_id,
            socket.gethostname(),
            lambda group: self.after(
                0,
                lambda: self._apply_local_display_change(source, group),
            ),
        )
        self._server_display_monitor = monitor
        try:
            monitor.start(initial_group)
        except Exception as error:
            self._server_display_monitor = None
            logger.error(
                "Could not start Server display monitor (%s)",
                error_name(error),
            )
            return False
        return True

    def _stop_server_display_monitor(self):
        monitor = self.__dict__.get("_server_display_monitor")
        self._server_display_monitor = None
        if monitor is not None:
            monitor.stop()

    def _apply_local_display_change(self, source, group):
        if self.server is not source:
            return False
        refreshed = self.topology_editor.refresh_machine(group)
        if refreshed:
            self._show_display_change_warning(group)
        return refreshed

    def _show_display_change_warning(self, group):
        toast = self.__dict__.get("display_warning_toast")
        if toast is not None:
            toast.show(group)
        self._set_status(
            f"Status: {group.windows_name} displays changed. "
            "Press Apply to rebuild mouse routing.",
            "orange",
        )

    def _show_client_disconnect_warning(self, windows_name):
        toast = self.__dict__.get("display_warning_toast")
        if toast is not None:
            toast.show_connection_lost(windows_name)

    def _hide_connection_toasts(self):
        topology_toast = self.__dict__.get("topology_toast")
        if topology_toast is not None:
            topology_toast.hide()
        warning_toast = self.__dict__.get("display_warning_toast")
        if warning_toast is not None:
            warning_toast.hide()

    def _intentional_disconnect_ids(self):
        session_ids = self.__dict__.get(
            "_intentional_disconnect_session_ids"
        )
        if session_ids is None:
            session_ids = set()
            self._intentional_disconnect_session_ids = session_ids
        return session_ids

    def stop_server(self):
        self._is_reloading = False
        self._stop_server_display_monitor()
        self._hide_connection_toasts()
        if self.server:
            server = self.server
            registry = getattr(server, "session_registry", None)
            if registry is not None:
                self._intentional_disconnect_ids().update(
                    session.session_id
                    for session in registry.active_sessions()
                )
            self._server_stopping = True
            try:
                if getattr(server, 'control_connected', False) and getattr(server, 'control_network', None):
                    try:
                        self._notify_ready_clients(
                            server,
                            {
                                'type': 'disconnect_notice',
                                'reason': 'server_stopping',
                            },
                        )
                        import time
                        time.sleep(0.05)
                    except Exception:
                        pass
                server.stop()
                self.server = None
            finally:
                self._server_stopping = False
        self.server_stop_btn.pack_forget()
        self.server_start_btn.pack(pady=10)
        self._set_status("Status: Server stopped", "gray")
        self.ensure_visible()

    def disconnect_client(self, target_client=None):
        if target_client is not None and self.client is not target_client:
            return
        if target_client is None:
            self._is_reloading = False
        self._hide_connection_toasts()
        client = self.client if target_client is None else target_client
        if self.client is client:
            self.client = None
        if client:
            if getattr(client, 'control_connected', False) and getattr(client, 'control_network', None):
                try:
                    client.control_network.send_message({'type': 'disconnect_notice', 'reason': 'client_disconnecting'})
                    import time
                    time.sleep(0.05)
                except Exception:
                    pass
            client.disconnect()
        if self.client is None:
            self.client_disconnect_btn.pack_forget()
            self.client_connect_btn.pack(pady=10)
            self.client_connect_btn.configure(state="normal")
            self._set_status("Status: Disconnected", "gray")
            self.ensure_visible()

    def reconnect_client(self):
        logger.info("GUI: Initiating client reconnect...")
        self._is_reloading = True
        self.after(3000, lambda: setattr(self, '_is_reloading', False))
        old_client = self.client
        self._set_status("Status: Reloading connection...", "orange")
        if old_client:
            try:
                old_client.disconnect()
            except Exception:
                pass
        self.after(500, self.connect_client)

    def _on_server_client_connected(self, data):
        self.after(0, lambda: self._set_status("Status: Client Connected!", "green"))

    def _on_server_display_inventory(self, source, data):
        if self.server is not source:
            return
        try:
            group = display_group_from_message(data.get("inventory"))
        except Exception as error:
            logger.error("Rejected Client display inventory (%s)", error_name(error))
            return
        session_id = data.get("session_id")
        registry = getattr(source, "session_registry", None)
        session = registry.get(session_id) if registry is not None and session_id else None
        if session_id is not None and session is None:
            logger.warning("Ignored display inventory from a closed Client session")
            return
        if session is not None and group.machine_id != session.peer_identity:
            logger.warning("Rejected display inventory whose machine identity did not match its session")
            return
        if session is not None:
            session.display_inventory = group
        display_changed = data.get("reason") == "display_changed"

        def add_to_draft():
            if self.server is not source:
                return
            if not self.topology_editor.add_client(group):
                self._set_status(
                    "Status: Client connected but no free topology position is available.",
                    "orange",
                )
                return
            if session is not None:
                self.topology_editor.set_client_color(
                    group.machine_id,
                    session.color,
                )
                placement = session.draft_placement
                if placement is not None:
                    self.topology_editor.state.move_machine(
                        group.machine_id,
                        placement[0],
                        placement[1],
                    )
                    self.topology_editor._render()
            if (
                session is None
                and self.topology_editor.state.active.version == 0
                and len(self.topology_editor.state.active.machines) == 1
            ):
                legacy_positions = {
                    "right": (1, 0),
                    "left": (-1, 0),
                    "top": (0, -1),
                    "bottom": (0, 1),
                }
                x, y = legacy_positions[self.legacy_layout_position]
                self.topology_editor.state.move_machine(group.machine_id, x, y)
                self.topology_editor._render()
            server = self.server
            if (
                not display_changed
                and server is not None
                and server.control_connected
            ):
                color = (
                    session.color
                    if session is not None
                    else self.topology_editor.state.client_color(group.machine_id)
                )
                message = {
                    "type": "topology_identify",
                    "color": color,
                    "connection_state": "connected",
                }
                if session_id is None:
                    server.control_network.send_message(message)
                else:
                    server.control_network.send_message(
                        message,
                        session_id=session_id,
                    )
            if display_changed:
                self._show_display_change_warning(group)
            self._record_topology_rescan_inventory(source, session_id)

        self.after(0, add_to_draft)

    def _on_topology_identify(self, source, data):
        def show():
            if self.client is not source or source.display_group is None:
                return
            self.topology_toast.show(
                source.display_group,
                data.get("color", CLIENT_COLORS[0]),
                data.get("connection_state", "connected"),
            )

        self.after(0, show)

    def _on_candidate_pending_toast(self, source, data):
        def show():
            if self.client is not source:
                return
            if source.display_group is None:
                try:
                    source.display_group = source.display_discovery.discover(
                        source.machine_id,
                        source.windows_name,
                    )
                except Exception as error:
                    logger.error(
                        "Could not identify pending Client displays (%s)",
                        error_name(error),
                    )
                    return
            self.topology_toast.show(
                source.display_group,
                data.get("color", PENDING_CLIENT_COLOR),
                "waiting for Server",
            )

        self.after(0, show)

    def _on_server_candidate_pending(self, source, data):
        if self.server is not source:
            return
        self.after(
            0,
            lambda: self._show_replacement_prompt(source, data),
        )

    def _show_replacement_prompt(self, source, data):
        if self.server is not source:
            return
        candidate_id = data.get("candidate_id") or data.get("session_id")
        pending = source.session_registry.pending_candidate()
        if pending is None or pending.session_id != candidate_id:
            return
        previous = self.__dict__.get("_replacement_dialog")
        if previous is not None:
            try:
                previous.destroy()
            except Exception:
                pass
        dialog = ctk.CTkToplevel(self)
        self._replacement_dialog = dialog
        dialog.title("Two-Client Limit")
        dialog.geometry("360x260")
        dialog.resizable(False, False)
        dialog.transient(self)
        ctk.CTkLabel(
            dialog,
            text=(
                f"{pending.label} wants to connect.\n"
                "Conduit supports two Clients. Choose one to replace.\n"
                "This request closes automatically in 15 seconds."
            ),
            wraplength=320,
            justify="left",
        ).pack(fill="x", padx=20, pady=(20, 12))

        def choose(decision, replace_session_id=None):
            try:
                self._resolve_replacement_candidate(
                    source,
                    candidate_id,
                    decision,
                    replace_session_id=replace_session_id,
                )
            except (KeyError, RuntimeError, ValueError):
                pass
            finally:
                if self.__dict__.get("_replacement_dialog") is dialog:
                    self._replacement_dialog = None
                try:
                    dialog.destroy()
                except Exception:
                    pass

        for session in source.session_registry.active_sessions():
            ctk.CTkButton(
                dialog,
                text=f"Replace {session.label}",
                command=lambda session_id=session.session_id: choose(
                    CandidateDecision.REPLACE,
                    session_id,
                ),
            ).pack(fill="x", padx=34, pady=4)
        ctk.CTkButton(
            dialog,
            text="Reject",
            fg_color="#374151",
            command=lambda: choose(CandidateDecision.REJECT),
        ).pack(fill="x", padx=34, pady=4)
        dialog.protocol(
            "WM_DELETE_WINDOW",
            lambda: choose(CandidateDecision.REJECT),
        )
        dialog.after(
            15000,
            lambda: choose(CandidateDecision.REJECT),
        )

    def _resolve_replacement_candidate(
        self,
        source,
        candidate_id,
        decision,
        *,
        replace_session_id=None,
    ):
        if self.server is not source:
            return None
        registry = source.session_registry
        pending = registry.pending_candidate()
        if pending is None or pending.session_id != candidate_id:
            return None
        placement = None
        replaced_machine_id = None
        if CandidateDecision(decision) is CandidateDecision.REPLACE:
            target = registry.get(replace_session_id)
            if target is None:
                raise KeyError(replace_session_id)
            router = getattr(source, 'input_router', None)
            if router is not None:
                router.destination_lost(target.session_id)
            replaced_machine_id = target.peer_identity
            placement = next(
                (
                    (placed.x, placed.y)
                    for placed in self.topology_editor.state.draft.machines
                    if placed.group.machine_id == replaced_machine_id
                ),
                None,
            )
        resolution = registry.resolve_candidate(
            decision,
            replace_session_id=replace_session_id,
        )
        if resolution.outcome.value == "admitted":
            promoted = registry.get(resolution.session_id)
            if promoted is not None:
                promoted.draft_placement = placement
            if replaced_machine_id is not None:
                self.topology_editor.remove_client(replaced_machine_id)
        return resolution

    def _hide_topology_toast(self, source):
        if self.client is not source:
            return
        self.after(0, self.topology_toast.hide)

    def _begin_topology_rescan(self):
        server_id = self.topology_editor.state.draft.server_id
        try:
            server_group = WindowsDisplayDiscovery().discover(
                server_id,
                socket.gethostname(),
            )
        except Exception as error:
            logger.error("Could not rescan Server displays (%s)", error_name(error))
            self._set_status(
                "Status: Displays could not be rescanned. The previous layout is still active.",
                "red",
            )
            return False
        self.topology_editor.refresh_machine(server_group)
        monitor = self.__dict__.get("_server_display_monitor")
        if monitor is not None:
            monitor.update_baseline(server_group)

        server = self.server
        if server is None or not server.control_connected:
            return True
        sessions = tuple(server.session_registry.ready_sessions())
        if not sessions:
            return True
        pending = {
            "source": server,
            "waiting": frozenset(session.session_id for session in sessions),
            "received": set(),
        }
        self._pending_topology_rescan = pending
        sent = all(
            server.control_network.send_message(
                {"type": "display_inventory_request"},
                session_id=session.session_id,
            )
            for session in sessions
        )
        if not sent:
            self._pending_topology_rescan = None
            self._set_status(
                "Status: Client displays could not be rescanned. The previous layout is still active.",
                "red",
            )
            return False
        self._set_status("Status: Rescanning displays...", "orange")
        self.after(3000, lambda: self._expire_topology_rescan(pending))
        return False

    def _record_topology_rescan_inventory(self, source, session_id):
        pending = self.__dict__.get("_pending_topology_rescan")
        if not isinstance(pending, dict) or pending.get("source") is not source:
            return False
        if session_id not in pending["waiting"]:
            return False
        pending["received"].add(session_id)
        if pending["received"] != set(pending["waiting"]):
            return False
        self._pending_topology_rescan = None
        self.topology_editor.apply_current_draft()
        return True

    def _expire_topology_rescan(self, pending):
        if self.__dict__.get("_pending_topology_rescan") is not pending:
            return
        self._pending_topology_rescan = None
        self._set_status(
            "Status: Client display rescan timed out. The previous layout is still active.",
            "red",
        )

    def _on_topology_apply(self, result, candidate):
        if not result.is_valid:
            self._set_status(
                "Status: Layout is not connected. Move every Client onto a full grid edge.",
                "red",
            )
            return False
        mappings = tuple(
            mapping
            for mapping in candidate.edge_mappings
            if mapping.source_machine_id == candidate.server_id
            and mapping.destination_machine_id != candidate.server_id
        )
        server = self.server
        if server is not None and mappings and server.control_connected:
            self._set_status("Status: Applying machine layout...", "orange")

            def persist(topology):
                if self.server is not server:
                    return False
                self.preferences.save_active_topology(topology)
                return True

            server.apply_topology_candidate(
                candidate,
                on_persist=persist,
                on_complete=lambda success: self.after(
                    0,
                    lambda: self._finish_topology_apply(
                        server,
                        candidate,
                        success,
                    ),
                ),
            )
            return False
        try:
            self.preferences.save_active_topology(candidate)
            self._set_status("Status: Machine layout applied", "green")
            return True
        except Exception as error:
            logger.error("Could not apply topology (%s)", error_name(error))
            self._set_status(
                "Status: Layout could not be applied. The previous layout is still active.",
                "red",
            )
            return False

    def _finish_topology_apply(self, source, candidate, success):
        if self.server is not source:
            return
        if not success:
            self._set_status(
                "Status: Client did not accept the layout. The previous layout is still active.",
                "red",
            )
            return
        self.topology_editor.state.commit(candidate)
        applied_machine_ids = {
            placed.group.machine_id for placed in candidate.machines
        }
        for session in source.session_registry.active_sessions():
            if (
                session.peer_identity in applied_machine_ids
                and session.replacement_color is not None
                and source.session_registry.activate_replacement(
                    session.session_id
                )
            ):
                self.topology_editor.set_client_color(
                    session.peer_identity,
                    session.color,
                )
        self.topology_editor._render()
        if self.server is not None and self.server.control_connected:
            self._notify_ready_clients(
                self.server,
                {'type': 'topology_applied'},
            )
        self._set_status("Status: Machine layout applied", "green")

    def _on_topology_cancel(self):
        self._pending_topology_rescan = None
        server = self.server
        if server is not None and server.control_connected:
            self._notify_ready_clients(
                server,
                {'type': 'topology_cancelled'},
            )
        self._set_status("Status: Layout changes cancelled", "gray")

    @staticmethod
    def _notify_ready_clients(server, message):
        registry = getattr(server, "session_registry", None)
        if registry is None:
            return server.control_network.send_message(message)
        sessions = tuple(registry.ready_sessions())
        results = tuple(
            bool(server.control_network.send_message(
                message,
                session_id=session.session_id,
            ))
            for session in sessions
        )
        return all(results)
        
    def _on_server_client_disconnected(self, data):
        session_id = data.get("session_id")
        intentional_disconnect = bool(
            self.__dict__.get("_server_stopping", False)
            or (
                session_id is not None
                and session_id in self._intentional_disconnect_ids()
            )
        )
        if session_id is not None:
            self._intentional_disconnect_ids().discard(session_id)
        editor = self.__dict__.get("topology_editor")
        if editor is not None:
            machine_id = data.get("peer_identity")
            windows_name = data.get("windows_name")
            if machine_id and not windows_name:
                windows_name = next(
                    (
                        placed.group.windows_name
                        for placed in editor.state.draft.machines
                        if placed.group.machine_id == machine_id
                    ),
                    None,
                )

            def remove_disconnected_client():
                if machine_id:
                    editor.remove_client(machine_id)
                else:
                    editor.remove_clients_from_draft()
                if windows_name and not intentional_disconnect:
                    self._show_client_disconnect_warning(windows_name)

            self.after(0, remove_disconnected_client)
        source = self.server
        if source:
            port = self.server_port_entry.get()

            def show_listening_if_active():
                if self.server is source:
                    self._set_status(
                        f"Status: Server listening on port {port}",
                        "green",
                    )

            self.after(0, show_listening_if_active)
        self.ensure_visible()

    def _set_status(self, message, color="gray", white_text=None, show_ip=None):
        if show_ip is None:
            show_ip = message in ("Status: Idle", "Status: Server stopped", "Status: Disconnected")
        write_status_message(
            self.status_text,
            message,
            color,
            white_text=white_text,
            show_ip=show_ip,
        )

    def set_daemon_mode(self, hidden):
        """Set window visibility to background daemon mode (hidden) or visible mode."""
        def _apply():
            try:
                if hidden and self.state() != "withdrawn":
                    self.withdraw()
                    logger.info("[DAEMON] Conduit GUI hidden in background daemon mode.")
                elif not hidden and self.state() == "withdrawn":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                    logger.info("[DAEMON] Conduit GUI unhidden from background daemon mode.")
            except Exception as error:
                logger.debug("Could not set daemon mode: %s", error_name(error))
        self.after(0, _apply)

    def toggle_daemon_mode(self):
        """Toggle background daemon mode locally and sync across network with connected peer."""
        is_currently_hidden = (self.state() == "withdrawn")
        target_hidden = not is_currently_hidden
        self.set_daemon_mode(target_hidden)
        self._send_daemon_mode_sync(target_hidden)

    def _send_daemon_mode_sync(self, hidden):
        """Send background daemon mode visibility state to connected peer."""
        msg = {'type': 'set_daemon_mode', 'hidden': hidden}
        if self.server and getattr(self.server, 'control_connected', False) and getattr(self.server, 'control_network', None):
            try:
                if isinstance(self.server, ConduitServer):
                    self.server.broadcast_cluster_command(
                        'set_daemon_mode',
                        {'hidden': hidden},
                    )
                else:
                    self.server.control_network.send_message(msg)
            except Exception as error:
                logger.debug("Could not send daemon mode sync from server: %s", error_name(error))
        elif self.client and getattr(self.client, 'control_connected', False) and getattr(self.client, 'control_network', None):
            try:
                self.client.control_network.send_message(msg)
            except Exception as error:
                logger.debug("Could not send daemon mode sync from client: %s", error_name(error))

    def _on_remote_daemon_mode(self, data):
        """Handle daemon mode sync message from remote peer."""
        hidden = data.get('hidden', False)
        self.set_daemon_mode(hidden)
        if self.server and data.get("peer_identity"):
            self.server.broadcast_cluster_command(
                "set_daemon_mode",
                {"hidden": hidden},
            )

    def ensure_visible(self):
        """Ensure the GUI window is restored to visible state (unless connection reload is in progress)."""
        if self.__dict__.get('_is_reloading', False):
            logger.info("[DAEMON] Skipping window restore because connection reload is in progress.")
            return

        def _show():
            try:
                if self.state() == "withdrawn":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                    logger.info("[DAEMON] Conduit GUI restored to visibility.")
            except Exception as error:
                logger.debug("Could not ensure window visibility: %s", error_name(error))
        self.after(0, _show)

    def _on_emergency_exit_global(self):
        logger.warning(
            "[GUI] Global emergency exit triggered "
            "(Ctrl+Shift+Alt+Escape). Closing Conduit on both peers."
        )
        self._coordinate_app_shutdown(notify_peer=True)

    def _on_remote_app_shutdown(self, data):
        logger.warning("Authenticated peer requested Conduit shutdown.")
        self._coordinate_app_shutdown(
            notify_peer=bool(self.server and data.get("peer_identity"))
        )

    def _coordinate_app_shutdown(self, notify_peer):
        lock = self.__dict__.get("_shutdown_lock")
        if lock is None:
            lock = threading.Lock()
            self._shutdown_lock = lock
        with lock:
            if self.__dict__.get("_shutdown_started", False):
                return False
            self._shutdown_started = True

        self._is_reloading = False
        endpoints = [endpoint for endpoint in (self.server, self.client) if endpoint]
        for endpoint in endpoints:
            try:
                endpoint.prepare_app_shutdown()
            except Exception as error:
                logger.debug(
                    "Could not prepare endpoint for application shutdown (%s)",
                    error_name(error),
                )
        if notify_peer:
            endpoint = None
            if self.server and getattr(self.server, "control_connected", False):
                endpoint = self.server
            elif self.client and getattr(self.client, "control_connected", False):
                endpoint = self.client
            if endpoint is not None:
                try:
                    if isinstance(endpoint, ConduitServer):
                        endpoint.broadcast_cluster_command("shutdown_app")
                    else:
                        endpoint.control_network.send_message({"type": "shutdown_app"})
                except Exception as error:
                    logger.debug(
                        "Could not notify peer of application shutdown (%s)",
                        error_name(error),
                    )

        self.after(0, self.on_close)
        return True

    def _on_reload_connection_global(self):
        logger.warning("[GUI] Global connection reload triggered (Ctrl+Shift+Alt+R). Maintaining current window visibility.")
        self._is_reloading = True
        self.after(3000, lambda: setattr(self, '_is_reloading', False))
        if self.server:
            self.server._reload_connection()
        elif self.client:
            requester = getattr(self.client, "request_cluster_reload", None)
            if requester is None:
                self.reconnect_client()
            else:
                requester()

    def _on_remote_reload_connection(self, data):
        logger.info("[GUI] Remote peer triggered connection reload. Maintaining current window visibility.")
        self._is_reloading = True
        self.after(3000, lambda: setattr(self, '_is_reloading', False))
        if self.server and data.get("peer_identity"):
            self.server._reload_connection()

    def _on_disconnect_notice(self, data):
        reason = data.get('reason', '')
        if reason == 'reload_connection' or self.__dict__.get('_is_reloading', False):
            logger.info("[GUI] Pre-disconnect notice received during connection reload. Maintaining window visibility.")
            return
        if reason in {'server_stopping', 'client_disconnecting'}:
            session_id = data.get("session_id")
            if session_id is not None:
                self._intentional_disconnect_ids().add(session_id)
            if reason == 'server_stopping':
                self._expected_server_stop = True
            logger.info(
                "[GUI] Intentional peer shutdown notice received (%s). "
                "Clearing connection toasts.",
                reason,
            )
            self.after(0, self._hide_connection_toasts)
        logger.info("[GUI] Pre-disconnect notice received from peer. Restoring window visibility.")
        self.ensure_visible()

    def _approve_fingerprint(self, fingerprint, peer):
        return self.pairing_approval.request(fingerprint, peer)

    def clear_client_trust(self):
        try:
            host = self.client_ip_entry.get()
            port = int(self.client_port_entry.get())
            store = self.client.trust_store if self.client else None
            if store is None:
                from app.trust import PeerTrustStore
                store = PeerTrustStore()
            cleared = store.clear(store.peer_id(host, port))
            message = "Saved identity cleared. Connect again to re-pair." if cleared else "No saved identity existed for this server."
            self._set_status(message, "orange")
        except Exception as error:
            self._set_status(
                public_error_message(error, "could not clear saved identity"),
                "red",
            )

    def _on_client_disconnected_event(self, source, data):
        self.after(
            0,
            lambda: self._finish_client_disconnect(source, data),
        )

    def _finish_client_disconnect(self, source, data=None):
        if self.client is not source:
            return
        data = data or {}
        session_id = data.get("session_id")
        intentional_disconnect = bool(
            self.__dict__.get("_expected_server_stop", False)
            or (
                session_id is not None
                and session_id in self._intentional_disconnect_ids()
            )
        )
        self._expected_server_stop = False
        if session_id is not None:
            self._intentional_disconnect_ids().discard(session_id)
        self.disconnect_client(target_client=source)
        if not intentional_disconnect:
            self._show_client_disconnect_warning("Server")
        self.ensure_visible()

    def _on_transfer_status(self, status):
        self.after(0, lambda: self.transfer_toast.show(status))

    def _cancel_transfer(self, job_id):
        if self.server and self.server.transfer_controller.status(job_id):
            return self.server.cancel_transfer(job_id)
        if self.client and self.client.transfer_controller.status(job_id):
            return self.client.cancel_transfer(job_id)
        return False

    def on_close(self):
        if self.__dict__.get("_close_started", False):
            return
        self._close_started = True
        self.pairing_approval.shutdown()
        monitor = self.__dict__.get('global_hotkey_monitor')
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        if self.overlay:
            self.hide_overlay()
        self._stop_server_display_monitor()
        if self.server:
            self.server.stop()
        if self.client:
            client, self.client = self.client, None
            client.disconnect()
        self.destroy()

    def _init_overlay(self):
        from app.input_geometry import windows_work_area, work_area_geometry

        self.overlay = ctk.CTkToplevel(self)
        left, top, right, bottom = windows_work_area()
        self.overlay.geometry(work_area_geometry((left, top, right, bottom)))
        self.overlay.overrideredirect(True)
        self.overlay_center_x = (right - left) // 2
        self.overlay_center_y = (bottom - top) // 2
        self.overlay.attributes("-alpha", 0.01) # Almost invisible
        self.overlay.config(cursor="none") # Hide host cursor
        self.overlay.attributes("-topmost", True)
        
        # Bind events
        self.overlay.bind("<Motion>", self.on_overlay_motion)
        self.overlay.bind("<ButtonPress>", self.on_overlay_press)
        self.overlay.bind("<ButtonRelease>", self.on_overlay_release)
        self.overlay.bind("<MouseWheel>", self.on_overlay_scroll)
        self.overlay.bind("<FocusOut>", self.on_overlay_focus_out)
        
        self.overlay.withdraw() # Hide it initially
        self.last_x = self.overlay_center_x
        self.last_y = self.overlay_center_y
        self.warp_count = 0

    def show_overlay(self):
        def _show():
            try:
                if self.overlay and self.overlay.winfo_exists():
                    self.overlay_active = True
                    self.overlay.deiconify() # Show it
                    self.overlay.focus_force()
                    self.overlay.grab_set()
                    self.transfer_toast.raise_if_visible()
                    
                    # Initial position
                    self.last_x = self.overlay_center_x
                    self.last_y = self.overlay_center_y
                    self.warp_count = 2
                    self.overlay.event_generate('<Motion>', warp=True, x=self.overlay_center_x, y=self.overlay_center_y)
            except Exception as error:
                logger.debug("Could not show overlay: %s", error_name(error))
        self.after(0, _show)

    def hide_overlay(self):
        def _hide():
            try:
                if self.overlay and self.overlay.winfo_exists():
                    self.overlay_active = False
                    try:
                        self.overlay.grab_release()
                    except Exception:
                        pass
                    self.overlay.withdraw()
            except Exception as error:
                logger.debug("Could not hide overlay: %s", error_name(error))
        self.after(0, _hide)

    def on_overlay_motion(self, event):
        if self.warp_count > 0:
            self.warp_count -= 1
            self.last_x = event.x
            self.last_y = event.y
            return

        dx = event.x - self.last_x
        dy = event.y - self.last_y
        
        # Flawless warp artifact filter: 
        # A jump of > 50 pixels is impossible for normal mouse movement in a few ms.
        # This perfectly filters out the -100px jump caused by the warp below!
        if abs(dx) > 50 or abs(dy) > 50:
            self.last_x = event.x
            self.last_y = event.y
            return
            
        if dx != 0 or dy != 0:
            if self.server:
                self.server.on_mouse_move(dx, dy)
                
            # If we get too close to the edges of the overlay, re-center the mouse 
            if abs(event.x - self.overlay_center_x) > 100 or abs(event.y - self.overlay_center_y) > 100:
                self.warp_count = 1
                self.overlay.event_generate('<Motion>', warp=True, x=self.overlay_center_x, y=self.overlay_center_y)
                
            self.last_x = event.x
            self.last_y = event.y

    def on_overlay_press(self, event):
        if not self.server: return
        button_map = {1: 'left', 2: 'middle', 3: 'right'}
        btn = button_map.get(event.num)
        if btn:
            self.server.on_mouse_click(btn, True)

    def on_overlay_release(self, event):
        if not self.server: return
        button_map = {1: 'left', 2: 'middle', 3: 'right'}
        btn = button_map.get(event.num)
        if btn:
            self.server.on_mouse_click(btn, False)

    def on_overlay_scroll(self, event):
        if not self.server: return
        # Windows Tkinter reports scroll in event.delta (usually multiples of 120)
        dy = 1 if event.delta > 0 else -1
        self.server.on_mouse_scroll(0, dy)

    def on_overlay_focus_out(self, event):
        # If the user opens the Snipping Tool (Win+Shift+S) or Alt-Tabs natively,
        # the overlay loses focus. We MUST return the cursor to the Server automatically.
        if self.overlay_active and self.server and self.server.control_connected:
            logger.info("Overlay lost focus (e.g. Snipping Tool). Switching back to Server.")
            self.server.on_switch_back({'ratio': 0.5})

def run_mainloop(app):
    try:
        app.mainloop()
    finally:
        try:
            exists = app.winfo_exists()
        except AttributeError:
            exists = True
        except (RuntimeError, tkinter.TclError, Exception):
            exists = False
        if exists:
            app.on_close()


def run_gui():
    import ctypes
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE ensures winfo_screenheight matches pynput physical pixels
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
        
    ctk.set_appearance_mode("dark")
    app = ConduitGUI()
    run_mainloop(app)
