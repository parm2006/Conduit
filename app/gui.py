import customtkinter as ctk
import logging
from pathlib import Path
from tkinter import messagebox

from app.server import DeskFlowServer
from app.client import DeskFlowClient
from app.file_transfer.toast import TransferToast
from app.crypto import certificate_fingerprint, pairing_code_from_fingerprint
from app.pairing_dialog import PairingApprovalController
from app.safe_errors import error_name, public_error_message
from app.preferences import UserPreferences
from app.ports import DEFAULT_BASE_PORT
from app.global_hotkey import GlobalHotkeyMonitor
from app.firewall import FirewallInspection, FirewallRuleSpec, FirewallState
from app.firewall_onboarding import (
    FirewallOnboarding,
    FirewallSetupOutcome,
    firewall_display,
)

logger = logging.getLogger(__name__)

def configure_main_window(window):
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
        logger.error("Could not save successful DeskFlow role (%s)", error_name(error))
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
        "Allow DeskFlow Server on private local networks?\n\n"
        "Windows will allow this DeskFlow executable to receive TCP "
        f"connections on ports {spec.local_ports} from devices on your local "
        "network. Public networks remain blocked."
    )
    if spec.development_scope:
        message += (
            "\n\nThis development build runs through Python. Windows can "
            "restrict the rule to this Python executable, but not to the "
            "DeskFlow script alone. Packaged releases use a DeskFlow-specific "
            "rule."
        )
    return message


def _firewall_conflict_text(spec):
    message = (
        "Windows is blocking incoming DeskFlow connections.\n\n"
        "Repair will disable only the conflicting firewall rule for this "
        "exact executable, then verify DeskFlow's restricted rule.\n\n"
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
            "DeskFlow-specific executable."
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
    dialog.title("DeskFlow Firewall")
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


class DeskFlowGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("DeskFlow")
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
        saved_position = self.preferences.load_client_position()
        self.known_hosts = self.load_known_hosts()
        self.overlay_center_x = self.winfo_screenwidth() // 2
        self.overlay_center_y = self.winfo_screenheight() // 2
        self.overlay = None
        self.overlay_active = False
        self._is_reloading = False
        self.transfer_toast = TransferToast(self, self._cancel_transfer)
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
        self.server_password_entry = ctk.CTkEntry(self.tab_server, show="*")
        self.server_password_entry.pack(pady=2)
        enable_textbox_qol(self.server_password_entry)
        
        # Layout Selection
        self.layout_label = ctk.CTkLabel(self.tab_server, text="Client Position:")
        self.layout_label.pack(pady=5)
        
        self.layout_frame = ctk.CTkFrame(self.tab_server, fg_color="transparent")
        self.layout_frame.pack(pady=5)
        
        self.layout_btns = {}
        
        # Center server block
        self.server_btn = ctk.CTkButton(self.layout_frame, text="S", width=40, height=40, fg_color="#555555", state="disabled")
        self.server_btn.grid(row=1, column=1, padx=5, pady=5)
        
        self.layout_btns['top'] = ctk.CTkButton(self.layout_frame, text="", width=40, height=40, fg_color="#333333", command=lambda: self.set_layout_position('top'))
        self.layout_btns['top'].grid(row=0, column=1, padx=5, pady=5)
        
        self.layout_btns['left'] = ctk.CTkButton(self.layout_frame, text="", width=40, height=40, fg_color="#333333", command=lambda: self.set_layout_position('left'))
        self.layout_btns['left'].grid(row=1, column=0, padx=5, pady=5)
        
        self.layout_btns['right'] = ctk.CTkButton(self.layout_frame, text="C", width=40, height=40, fg_color="white", text_color="black", command=lambda: self.set_layout_position('right'))
        self.layout_btns['right'].grid(row=1, column=2, padx=5, pady=5)
        
        self.layout_btns['bottom'] = ctk.CTkButton(self.layout_frame, text="", width=40, height=40, fg_color="#333333", command=lambda: self.set_layout_position('bottom'))
        self.layout_btns['bottom'].grid(row=2, column=1, padx=5, pady=5)
        
        self.layout_position = 'right'
        self.set_layout_position(saved_position, persist=False)
        
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
        self.client_password_entry = ctk.CTkEntry(self.tab_client, show="*")
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
        messagebox.showinfo("DeskFlow Firewall", display.explanation)

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
            "Repair DeskFlow Firewall"
            if conflict
            else "Configure DeskFlow Firewall",
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
            self.server.stop()
            
        if not self.overlay:
            self._init_overlay()
            
        self.server = DeskFlowServer(
            password=password, 
            port=port, 
            layout_position=self.layout_position,
            on_capture_start=self.show_overlay, 
            on_capture_stop=self.hide_overlay,
            on_transfer_status=self._on_transfer_status,
        )
        self.server.control_network.register_callback('connected', self._on_server_client_connected)
        self.server.control_network.register_callback('disconnected', self._on_server_client_disconnected)
        self.server.control_network.register_callback('set_daemon_mode', self._on_remote_daemon_mode)
        self.server.control_network.register_callback('disconnect_notice', self._on_disconnect_notice)
        self.server.control_network.register_callback('reload_connection', self._on_remote_reload_connection)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.server.set_screen_size(screen_width, screen_height)
        
        if self.server.start():
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
            
        client = DeskFlowClient(
            password=password,
            on_transfer_status=self._on_transfer_status,
            fingerprint_approval=self._approve_fingerprint,
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
            self._set_status(f"Status: Connection failed\n{error_msg}", "red")

    def stop_server(self):
        self._is_reloading = False
        if self.server:
            if getattr(self.server, 'control_connected', False) and getattr(self.server, 'control_network', None):
                try:
                    self.server.control_network.send_message({'type': 'disconnect_notice', 'reason': 'server_stopping'})
                    import time
                    time.sleep(0.05)
                except Exception:
                    pass
            self.server.stop()
            self.server = None
        self.server_stop_btn.pack_forget()
        self.server_start_btn.pack(pady=10)
        self._set_status("Status: Server stopped", "gray")
        self.ensure_visible()

    def disconnect_client(self, target_client=None):
        if target_client is not None and self.client is not target_client:
            return
        if target_client is None:
            self._is_reloading = False
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
        
    def _on_server_client_disconnected(self, data):
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

    def set_layout_position(self, position, persist=True):
        if position not in {"top", "left", "right", "bottom"}:
            position = "right"
        self.layout_position = position
        for candidate, button in self.layout_btns.items():
            if candidate == position:
                button.configure(text="C", fg_color="white", text_color="black")
            else:
                button.configure(text="", fg_color="#333333")
        if persist:
            try:
                self.preferences.save_client_position(position)
            except Exception as error:
                logger.error(
                    "Could not save client position preference (%s)",
                    error_name(error),
                )

    def set_daemon_mode(self, hidden):
        """Set window visibility to background daemon mode (hidden) or visible mode."""
        def _apply():
            try:
                if hidden and self.state() != "withdrawn":
                    self.withdraw()
                    logger.info("[DAEMON] DeskFlow GUI hidden in background daemon mode.")
                elif not hidden and self.state() == "withdrawn":
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                    logger.info("[DAEMON] DeskFlow GUI unhidden from background daemon mode.")
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
                    logger.info("[DAEMON] DeskFlow GUI restored to visibility.")
            except Exception as error:
                logger.debug("Could not ensure window visibility: %s", error_name(error))
        self.after(0, _show)

    def _on_emergency_exit_global(self):
        logger.warning("[GUI] Global emergency exit triggered (Ctrl+Shift+Alt+Escape). Restoring window visibility.")
        self._is_reloading = False
        if self.server:
            self.server._on_emergency_exit()
        if self.client:
            self.client.disconnect()
        self.hide_overlay()
        self.ensure_visible()

    def _on_reload_connection_global(self):
        logger.warning("[GUI] Global connection reload triggered (Ctrl+Shift+Alt+R). Maintaining current window visibility.")
        self._is_reloading = True
        self.after(3000, lambda: setattr(self, '_is_reloading', False))
        if self.server:
            self.server._reload_connection()
        elif self.client:
            self.reconnect_client()

    def _on_remote_reload_connection(self, data):
        logger.info("[GUI] Remote peer triggered connection reload. Maintaining current window visibility.")
        self._is_reloading = True
        self.after(3000, lambda: setattr(self, '_is_reloading', False))

    def _on_disconnect_notice(self, data):
        reason = data.get('reason', '')
        if reason == 'reload_connection' or self.__dict__.get('_is_reloading', False):
            logger.info("[GUI] Pre-disconnect notice received during connection reload. Maintaining window visibility.")
            return
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
        self.after(0, lambda: self._finish_client_disconnect(source))

    def _finish_client_disconnect(self, source):
        if self.client is source:
            self.disconnect_client(target_client=source)
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
        self.pairing_approval.shutdown()
        monitor = self.__dict__.get('global_hotkey_monitor')
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        if self.overlay:
            self.hide_overlay()
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
    app = DeskFlowGUI()
    run_mainloop(app)
