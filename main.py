"""
main.py — Skin Importer (Linux/Proton fork)

Changes from the upstream Windows version
------------------------------------------
1.  winreg / ctypes.windll removed entirely.
2.  On startup, the app auto-detects the PG3D Proton prefix via
    proton_path.find_proton_prefix().  If detection fails the app still
    opens but most actions are blocked until the user sets the path in
    Settings.
3.  skinimporter.init(prefix) is called once a valid prefix is known.
4.  "Copy Skins" tab replaced with "Browse Skins" — a local folder
    browser that loads .png files from any directory you point it at.
    No network calls are made anywhere in this fork.
5.  Settings tab gains a Proton Prefix section: shows the detected/saved
    path, a manual-override picker, and a "Re-detect" button.
6.  Icon loading uses .png only (no .ico, which requires Windows).
7.  The app ID call (SetCurrentProcessExplicitAppUserModelID) is silently
    skipped (the try/except was already there in the original).
8.  Mouse wheel scrolling enabled on all scrollable frames.  Each frame
    gets its own per-canvas binding instead of bind_all, which prevents
    all frames scrolling simultaneously.
9.  File picker uses zenity (XDG portal, works on Wayland+X11), then
    kdialog (KDE native), then tkinter fallback.
10. Browse Skins folder path is persisted in the app config so it
    survives restarts.
"""

import io
import base64
import os
import shutil
import subprocess
import threading
import tkinter
import tkinter.filedialog
import tkinter.messagebox
import customtkinter
from PIL import Image
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

import proton_path as _pp
from skinimporter import add_skins, append_skin, clear_modded_skins, get_added_skins, delete_skin
import skinimporter as _si
from skin_utils import get_skin_size, is_valid_skin_size, is_skin_size_64x64, get_import_ready_skin_base64


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def log_message(message_type: str, title: str, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color = {
        "INFO": Fore.CYAN,
        "ERROR": Fore.RED,
        "WARNING": Fore.YELLOW,
    }.get(message_type, Fore.WHITE)
    print(f"{color}[{timestamp}] [{message_type}] {title}: {message}{Style.RESET_ALL}")


def show_info(title: str, message: str) -> None:
    log_message("INFO", title, message)
    tkinter.messagebox.showinfo(title, message)


def show_error(title: str, message: str) -> None:
    log_message("ERROR", title, message)
    tkinter.messagebox.showerror(title, message)


def show_warning(title: str, message: str) -> None:
    log_message("WARNING", title, message)
    tkinter.messagebox.showwarning(title, message)


# ---------------------------------------------------------------------------
# Native file/folder picker
# ---------------------------------------------------------------------------

def pick_files(title: str = "Select files", filetypes: str = "*.png") -> list[str]:
    """
    Open a file picker and return a list of selected file paths.
    Tries zenity (XDG portal — works on KDE/GNOME, Wayland and X11),
    then kdialog, then tkinter fallback.
    """
    # zenity via XDG desktop portal — best cross-DE support
    if shutil.which("zenity"):
        try:
            result = subprocess.run(
                ["zenity", "--file-selection", "--multiple",
                 "--file-filter", f"PNG files | {filetypes}",
                 "--title", title],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                # zenity separates multiple paths with '|'
                return [p for p in result.stdout.strip().split("|") if p]
        except Exception:
            pass

    # kdialog — KDE native
    if shutil.which("kdialog"):
        try:
            result = subprocess.run(
                ["kdialog", "--title", title, "--getopenfilename",
                 os.path.expanduser("~"), filetypes, "--multiple"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                return [p for p in result.stdout.strip().splitlines() if p]
        except Exception:
            pass

    # tkinter fallback
    paths = tkinter.filedialog.askopenfilenames(
        title=title,
        filetypes=[("PNG files", "*.png"), ("All files", "*.*")]
    )
    return list(paths)


def pick_folder(title: str = "Select folder") -> str | None:
    """
    Open a folder picker and return the chosen path, or None if cancelled.
    Tries zenity (XDG portal — works on KDE/GNOME, Wayland and X11),
    then kdialog, then tkinter fallback.
    """
    # zenity — XDG desktop portal, opens Dolphin on KDE Plasma 6
    if shutil.which("zenity"):
        try:
            result = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title", title],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            if result.returncode == 1:
                return None  # user cancelled; don't fall through to next picker
        except Exception:
            pass

    # kdialog — KDE native fallback
    if shutil.which("kdialog"):
        try:
            result = subprocess.run(
                ["kdialog", "--title", title, "--getexistingdirectory",
                 os.path.expanduser("~")],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            if result.returncode == 1:
                return None
        except Exception:
            pass

    # tkinter fallback
    folder = tkinter.filedialog.askdirectory(title=title)
    return folder or None


# ---------------------------------------------------------------------------
# Mouse wheel scroll binding (per-canvas, avoids bind_all conflicts)
# ---------------------------------------------------------------------------

def _bind_mousewheel(widget: customtkinter.CTkScrollableFrame) -> None:
    """
    Bind mouse wheel events directly to the scrollable frame's internal canvas.

    Using bind_all() (as the original code did) attaches the handler to the
    root window, so *every* scroll frame scrolls when any one is moused over.
    Binding directly to the canvas/widget avoids that cross-tab interference.
    """
    canvas = getattr(widget, "_parent_canvas", None)
    if canvas is None:
        return

    def on_wheel(event: tkinter.Event) -> None:
        # X11 sends Button-4 (up) / Button-5 (down)
        if event.num == 4:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            canvas.yview_scroll(1, "units")
        else:
            # Some setups / Wayland send delta-based events
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
        canvas.bind(seq, on_wheel)
        widget.bind(seq, on_wheel)


# ---------------------------------------------------------------------------
# App constants
# ---------------------------------------------------------------------------

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

BUTTON_FG_COLOR = "#7C3AED"
BUTTON_HOVER_COLOR = "#5B21B6"
BUTTON_TEXT_COLOR = "white"

VERSION = "1.3-linux"

# Config key for the persisted browse-skins folder
_CFG_BROWSE_FOLDER = "browse_skins_folder"


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(customtkinter.CTk):
    def __init__(self) -> None:
        super().__init__()

        self.title(f"Skin Importer v{VERSION}")
        self.geometry("1100x580")

        # --- icon (PNG only; .ico requires Windows) ---
        icon_png_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(icon_png_path):
            try:
                self._taskbar_icon_image = tkinter.PhotoImage(file=icon_png_path)
                self.iconphoto(True, self._taskbar_icon_image)
            except Exception:
                pass

        # --- resolve Proton prefix ---
        self._prefix_path: str | None = _pp.find_proton_prefix()
        if self._prefix_path:
            log_message("INFO", "Proton", f"Prefix found: {self._prefix_path}")
            _si.init(self._prefix_path)
        else:
            log_message("WARNING", "Proton", "Proton prefix not found — please set it in Settings.")

        # --- layout ---
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # sidebar
        self.sidebar_frame = customtkinter.CTkFrame(self, width=140, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1)

        self.logo_label = customtkinter.CTkLabel(
            self.sidebar_frame, text="Skin Importer",
            font=customtkinter.CTkFont(size=20, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        tab_names = ["Upload Skin", "Browse Skins", "Manage Skins", "Settings", "How to Use"]
        self.tab_buttons: list[customtkinter.CTkButton] = []
        for i, name in enumerate(tab_names):
            btn = customtkinter.CTkButton(
                self.sidebar_frame, text=name,
                fg_color=BUTTON_FG_COLOR,
                text_color=BUTTON_TEXT_COLOR,
                hover_color=BUTTON_HOVER_COLOR,
                anchor="w",
                command=lambda idx=i: self.select_tab(idx)
            )
            btn.grid(row=i + 1, column=0, padx=10, pady=5, sticky="ew")
            self.tab_buttons.append(btn)

        self.appearance_mode_label = customtkinter.CTkLabel(
            self.sidebar_frame, text="Appearance Mode:", anchor="w"
        )
        self.appearance_mode_label.grid(row=7, column=0, padx=20, pady=(10, 0))
        self.appearance_mode_optionemenu = customtkinter.CTkOptionMenu(
            self.sidebar_frame, values=["Light", "Dark", "System"],
            fg_color=BUTTON_FG_COLOR,
            button_color=BUTTON_FG_COLOR,
            button_hover_color=BUTTON_HOVER_COLOR,
            dropdown_fg_color=BUTTON_FG_COLOR,
            dropdown_hover_color=BUTTON_HOVER_COLOR,
            command=self.change_appearance_mode_event
        )
        self.appearance_mode_optionemenu.grid(row=8, column=0, padx=20, pady=(10, 10))

        # tab content frames
        self.tab_frames: list[customtkinter.CTkFrame] = []
        for i in range(len(tab_names)):
            frame = customtkinter.CTkFrame(self, corner_radius=0, fg_color="transparent")
            frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
            frame.grid_columnconfigure(0, weight=1)
            self.build_tab_content(frame, i)
            frame.grid_remove()
            self.tab_frames.append(frame)

        self.appearance_mode_optionemenu.set("Dark")

        self.notification_label = customtkinter.CTkLabel(self, text="")
        self._notification_after_id = None

        self.current_tab: int | None = None
        self.select_tab(0)

        # Warn user immediately if prefix is missing
        if not self._prefix_path:
            self.after(300, self._warn_no_prefix)

    # -----------------------------------------------------------------------
    # Prefix helpers
    # -----------------------------------------------------------------------

    def _warn_no_prefix(self) -> None:
        show_warning(
            "Proton Prefix Not Found",
            "Could not automatically detect the Pixel Gun 3D Proton prefix.\n\n"
            "Please go to Settings → Proton Prefix and set the path manually.\n\n"
            "Expected path structure:\n"
            "  ~/.steam/steam/steamapps/compatdata/1047820/pfx/"
        )

    def _apply_prefix(self, path: str) -> bool:
        if not _pp._is_valid_prefix(path):
            show_error(
                "Invalid Prefix",
                f"The selected folder does not look like a valid Proton prefix.\n\n"
                f"It must contain a file called 'user.reg'.\n\nChosen path:\n{path}"
            )
            return False
        _pp.save_prefix_path(path)
        self._prefix_path = path
        _si.init(path)
        log_message("INFO", "Proton", f"Prefix set to: {path}")
        return True

    # -----------------------------------------------------------------------
    # Tab builder
    # -----------------------------------------------------------------------

    def build_tab_content(self, frame: customtkinter.CTkFrame, index: int) -> None:
        if index == 0:
            self._build_upload_tab(frame)
        elif index == 1:
            self._build_browse_tab(frame)
        elif index == 2:
            self._build_manage_tab(frame)
        elif index == 3:
            self._build_settings_tab(frame)
        elif index == 4:
            self._build_howto_tab(frame)

    # -----------------------------------------------------------------------
    # Tab 0 — Upload Skin
    # -----------------------------------------------------------------------

    def _build_upload_tab(self, frame: customtkinter.CTkFrame) -> None:
        frame.grid_rowconfigure(3, weight=1)

        customtkinter.CTkLabel(
            frame, text="Upload Skins",
            font=customtkinter.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="ew")

        btn_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        btn_row.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="w")

        customtkinter.CTkButton(
            btn_row, text="Add Skins", width=120,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self.browse_skin_files
        ).grid(row=0, column=0, padx=(0, 10))

        customtkinter.CTkButton(
            btn_row, text="Clear All", width=100,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            border_width=2, text_color=BUTTON_TEXT_COLOR,
            command=self.clear_skin_files
        ).grid(row=0, column=1)

        import_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        import_row.grid(row=2, column=0, padx=10, pady=(0, 8), sticky="w")

        customtkinter.CTkButton(
            import_row, text="Import", width=100,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self.import_skin_files
        ).grid(row=0, column=0)

        self.preview_scroll = customtkinter.CTkScrollableFrame(frame, label_text="Selected Skins")
        self.preview_scroll.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self._preview_cols = 3
        self.preview_scroll.grid_columnconfigure((0, 1, 2), weight=1)
        self.preview_scroll._parent_canvas.bind("<Configure>", self._on_preview_scroll_resize)
        _bind_mousewheel(self.preview_scroll)

        self._preview_images: list = []
        self._preview_cards: list[dict] = []

    # -----------------------------------------------------------------------
    # Tab 1 — Browse Skins
    # -----------------------------------------------------------------------

    def _build_browse_tab(self, frame: customtkinter.CTkFrame) -> None:
        frame.grid_rowconfigure(3, weight=1)

        customtkinter.CTkLabel(
            frame, text="Browse Skins",
            font=customtkinter.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="ew")

        folder_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        folder_row.grid(row=1, column=0, padx=10, pady=(0, 4), sticky="ew")
        folder_row.grid_columnconfigure(1, weight=1)

        customtkinter.CTkLabel(folder_row, text="Skin folder:", anchor="w").grid(
            row=0, column=0, padx=(0, 8)
        )

        self.browse_folder_entry = customtkinter.CTkEntry(
            folder_row, placeholder_text="Select a folder containing .png skin files…"
        )
        self.browse_folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        customtkinter.CTkButton(
            folder_row, text="Browse…", width=100,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self._pick_skin_folder
        ).grid(row=0, column=2)

        self.browse_status_label = customtkinter.CTkLabel(
            frame, text="", anchor="w", font=customtkinter.CTkFont(size=11)
        )
        self.browse_status_label.grid(row=2, column=0, padx=10, pady=(0, 4), sticky="ew")

        self.browse_scroll = customtkinter.CTkScrollableFrame(frame, label_text="Skins in Folder")
        self.browse_scroll.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self._browse_cols = 3
        self._browse_skins_data: list[dict] = []
        self.browse_scroll.grid_columnconfigure((0, 1, 2), weight=1)
        self.browse_scroll._parent_canvas.bind("<Configure>", self._on_browse_scroll_resize)
        _bind_mousewheel(self.browse_scroll)

        self._browse_skin_images: list = []

        # Restore last-used folder from config
        saved_folder = _pp.load_config().get(_CFG_BROWSE_FOLDER, "")
        if saved_folder and os.path.isdir(saved_folder):
            self.browse_folder_entry.insert(0, saved_folder)
            self.after(100, lambda: self._load_skins_from_folder(saved_folder))

    def _pick_skin_folder(self) -> None:
        folder = pick_folder(title="Select folder containing skin PNGs")
        if not folder:
            return
        self.browse_folder_entry.delete(0, "end")
        self.browse_folder_entry.insert(0, folder)
        # Persist to config
        cfg = _pp.load_config()
        cfg[_CFG_BROWSE_FOLDER] = folder
        _pp.save_config(cfg)
        self._load_skins_from_folder(folder)

    def _load_skins_from_folder(self, folder: str) -> None:
        png_files = sorted(
            f for f in os.listdir(folder) if f.lower().endswith(".png")
        )
        if not png_files:
            self.browse_status_label.configure(
                text="No .png files found in this folder.",
                text_color=("#B45309", "#FBBF24")
            )
            for widget in self.browse_scroll.winfo_children():
                widget.destroy()
            self._browse_skins_data.clear()
            self._browse_skin_images.clear()
            return

        self.browse_status_label.configure(
            text=f"Found {len(png_files)} skin(s).",
            text_color=("gray10", "gray90")
        )

        skins = []
        for fname in png_files:
            full_path = os.path.join(folder, fname)
            try:
                with open(full_path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                skins.append({"name": fname, "path": full_path, "skin": b64})
            except OSError:
                continue

        self._browse_skins_data = skins
        self._populate_browse_results(skins)

    def _populate_browse_results(self, skins: list[dict]) -> None:
        for widget in self.browse_scroll.winfo_children():
            widget.destroy()
        self._browse_skin_images.clear()

        cols = self._browse_cols
        for i in range(20):
            self.browse_scroll.grid_columnconfigure(i, weight=0)
        for i in range(cols):
            self.browse_scroll.grid_columnconfigure(i, weight=1)

        for idx, skin in enumerate(skins):
            col = idx % cols
            row = idx // cols

            card = customtkinter.CTkFrame(self.browse_scroll)
            card.grid(row=row, column=col, padx=8, pady=8)

            try:
                pil_img = Image.open(skin["path"])
                w, h = pil_img.size
                display_h = int(h * 64 / w) if w > 0 else 64
                ctk_img = customtkinter.CTkImage(
                    light_image=pil_img, dark_image=pil_img,
                    size=(64, display_h)
                )
                self._browse_skin_images.append(ctk_img)
                customtkinter.CTkLabel(card, image=ctk_img, text="").grid(
                    row=0, column=0, padx=8, pady=(8, 4)
                )
            except Exception:
                pass

            customtkinter.CTkLabel(
                card, text=skin["name"],
                font=customtkinter.CTkFont(size=11), wraplength=100
            ).grid(row=1, column=0, padx=8, pady=(0, 4))

            customtkinter.CTkButton(
                card, text="Add", width=80, height=28,
                fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
                text_color=BUTTON_TEXT_COLOR,
                command=lambda b=skin["skin"], n=skin["name"]: self._add_browse_skin(b, n)
            ).grid(row=2, column=0, padx=8, pady=(0, 8))

        # Re-apply scroll binding after repopulating (children have changed)
        _bind_mousewheel(self.browse_scroll)

    def _add_browse_skin(self, skin_base64: str, skin_name: str) -> None:
        if not self._prefix_path:
            show_error("No Prefix", "Proton prefix not set. Go to Settings to fix this.")
            return
        try:
            skin_id = append_skin(skin_base64, skin_name)
        except OSError as error:
            show_error("Error", f"Could not write skin to registry.\n\n{error}")
            return
        self._show_notification(f"'{skin_name}' added as skin ID {skin_id}.", duration_ms=3500)

    def _on_browse_scroll_resize(self, event: tkinter.Event) -> None:
        cols = max(1, event.width // 120 - 1)
        if cols != self._browse_cols:
            self._browse_cols = cols
            if self._browse_skins_data:
                self._populate_browse_results(self._browse_skins_data)

    # -----------------------------------------------------------------------
    # Tab 2 — Manage Skins
    # -----------------------------------------------------------------------

    def _build_manage_tab(self, frame: customtkinter.CTkFrame) -> None:
        frame.grid_rowconfigure(4, weight=1)

        customtkinter.CTkLabel(
            frame, text="Manage Skins",
            font=customtkinter.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="ew")

        customtkinter.CTkLabel(
            frame, text="This is a list of skins you have modded",
            anchor="w", font=customtkinter.CTkFont(size=11)
        ).grid(row=1, column=0, padx=10, pady=(0, 4), sticky="ew")

        controls_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        controls_row.grid(row=2, column=0, padx=10, pady=(0, 4), sticky="w")

        customtkinter.CTkButton(
            controls_row, text="Refresh", width=100,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self.refresh_manage_skins
        ).grid(row=0, column=0, padx=(0, 8))

        self.manage_status_label = customtkinter.CTkLabel(
            frame, text="", anchor="w", font=customtkinter.CTkFont(size=11)
        )
        self.manage_status_label.grid(row=3, column=0, padx=10, pady=(0, 4), sticky="ew")

        self.manage_scroll = customtkinter.CTkScrollableFrame(frame, label_text="Added Skins")
        self.manage_scroll.grid(row=4, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self._manage_cols = 3
        self._manage_skins_data: list[dict] = []
        self.manage_scroll.grid_columnconfigure((0, 1, 2), weight=1)
        self.manage_scroll._parent_canvas.bind("<Configure>", self._on_manage_scroll_resize)
        _bind_mousewheel(self.manage_scroll)

        self._manage_skin_images: list = []

    # -----------------------------------------------------------------------
    # Tab 3 — Settings
    # -----------------------------------------------------------------------

    def _build_settings_tab(self, frame: customtkinter.CTkFrame) -> None:
        frame.grid_rowconfigure(10, weight=1)

        customtkinter.CTkLabel(
            frame, text="Settings",
            font=customtkinter.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="ew")

        customtkinter.CTkLabel(
            frame, text="Proton Prefix",
            anchor="w", font=customtkinter.CTkFont(size=13, weight="bold")
        ).grid(row=1, column=0, padx=10, pady=(8, 0), sticky="w")

        customtkinter.CTkLabel(
            frame,
            text="Path to your PG3D Proton prefix directory (must contain user.reg).",
            anchor="w", font=customtkinter.CTkFont(size=11)
        ).grid(row=2, column=0, padx=10, pady=(2, 4), sticky="w")

        prefix_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        prefix_row.grid(row=3, column=0, padx=10, pady=(0, 4), sticky="ew")
        prefix_row.grid_columnconfigure(0, weight=1)

        self.prefix_entry = customtkinter.CTkEntry(
            prefix_row, placeholder_text="e.g. ~/.steam/steam/steamapps/compatdata/1047820/pfx"
        )
        self.prefix_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        if self._prefix_path:
            self.prefix_entry.insert(0, self._prefix_path)

        customtkinter.CTkButton(
            prefix_row, text="Browse…", width=90,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self._browse_prefix_folder
        ).grid(row=0, column=1, padx=(0, 8))

        customtkinter.CTkButton(
            prefix_row, text="Re-detect", width=90,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self._redetect_prefix
        ).grid(row=0, column=2)

        customtkinter.CTkButton(
            frame, text="Save Prefix Path", width=160,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self._save_prefix_from_entry
        ).grid(row=4, column=0, padx=10, pady=(0, 8), sticky="w")

        self.prefix_status_label = customtkinter.CTkLabel(
            frame, text=self._prefix_status_text(),
            anchor="w", font=customtkinter.CTkFont(size=11)
        )
        self.prefix_status_label.grid(row=5, column=0, padx=10, pady=(0, 8), sticky="w")

        customtkinter.CTkButton(
            frame, text="Clear Modded Skins", width=180,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            text_color=BUTTON_TEXT_COLOR, command=self.clear_modded_skins
        ).grid(row=6, column=0, padx=10, pady=(0, 0), sticky="nw")

        customtkinter.CTkLabel(
            frame, text="Credits",
            anchor="w", font=customtkinter.CTkFont(size=13, weight="bold")
        ).grid(row=7, column=0, padx=10, pady=(8, 0), sticky="w")

        customtkinter.CTkLabel(
            frame,
            text="Claude - making 99% of this\nStella - for original player lookup tool",
            anchor="w", font=customtkinter.CTkFont(size=11)
        ).grid(row=8, column=0, padx=10, pady=(2, 0), sticky="w")

        customtkinter.CTkLabel(
            frame, text="made by YeetDisDude  •  Linux fork",
            anchor="w", font=customtkinter.CTkFont(size=11, weight="bold")
        ).grid(row=9, column=0, padx=10, pady=(10, 0), sticky="w")

    def _prefix_status_text(self) -> str:
        if self._prefix_path:
            return f"✔  Active prefix: {self._prefix_path}"
        return "✘  No prefix set — skin operations will fail."

    def _browse_prefix_folder(self) -> None:
        folder = pick_folder(
            title="Select Proton prefix folder (the 'pfx' directory containing user.reg)"
        )
        if folder:
            self.prefix_entry.delete(0, "end")
            self.prefix_entry.insert(0, folder)

    def _save_prefix_from_entry(self) -> None:
        path = self.prefix_entry.get().strip()
        if not path:
            show_error("No Path", "Please enter or browse to the prefix path first.")
            return
        path = os.path.expanduser(path)
        if self._apply_prefix(path):
            self.prefix_status_label.configure(text=self._prefix_status_text())
            show_info("Prefix Saved", f"Proton prefix saved and activated:\n{path}")

    def _redetect_prefix(self) -> None:
        cfg = _pp.load_config()
        cfg.pop("proton_prefix_path", None)
        _pp.save_config(cfg)

        found = _pp.find_proton_prefix()
        if found:
            self._apply_prefix(found)
            self.prefix_entry.delete(0, "end")
            self.prefix_entry.insert(0, found)
            self.prefix_status_label.configure(text=self._prefix_status_text())
            show_info("Re-detected", f"Prefix found:\n{found}")
        else:
            show_error(
                "Not Found",
                "Could not auto-detect the Proton prefix.\n"
                "Please browse to it manually and click Save."
            )

    # -----------------------------------------------------------------------
    # Tab 4 — How to Use
    # -----------------------------------------------------------------------

    def _build_howto_tab(self, frame: customtkinter.CTkFrame) -> None:
        rows = [
            ("1) Upload skins", "Add skin files (64x32, 64x64, or any 2:1 size), then click Import."),
            ("2) Browse Skins",
             "Pick a local folder of .png skin files and click Add on any skin you want.\n"
             "The last folder you used is remembered across restarts."),
            ("3) For skins to save permanently",
             "After importing, open the game, go to the skin editor, then save the skin — "
             "otherwise it is only stored on this device."),
            ("4) Reopen Pixel Gun 3D",
             "Close and reopen the game through Steam (Proton) for imported skins to appear."),
            ("Proton prefix",
             "If the app can't find your game, go to Settings and set the prefix path manually.\n"
             "It is usually:\n"
             "  ~/.steam/steam/steamapps/compatdata/1047820/pfx"),
        ]

        customtkinter.CTkLabel(
            frame, text="How to Use",
            font=customtkinter.CTkFont(size=18, weight="bold"), anchor="w"
        ).grid(row=0, column=0, padx=10, pady=(10, 2), sticky="ew")

        for i, (title, desc) in enumerate(rows):
            customtkinter.CTkLabel(
                frame, text=title, anchor="w",
                font=customtkinter.CTkFont(size=13, weight="bold")
            ).grid(row=1 + i * 2, column=0, padx=10, pady=(6, 2), sticky="w")
            customtkinter.CTkLabel(
                frame, text=desc, anchor="w", justify="left",
                font=customtkinter.CTkFont(size=12)
            ).grid(row=2 + i * 2, column=0, padx=10, pady=(0, 4), sticky="w")

    # -----------------------------------------------------------------------
    # Tab 0 logic
    # -----------------------------------------------------------------------

    def browse_skin_files(self) -> None:
        paths = pick_files(title="Select skin files", filetypes="*.png")
        for path in paths:
            if any(entry["path"] == path for entry in self._preview_cards):
                continue
            self._add_skin_preview(path)

    def _add_skin_preview(self, path: str) -> None:
        image_size = get_skin_size(path)
        preview_width = 128
        preview_height = max(1, int(round(preview_width * (image_size[1] / image_size[0]))))
        img = Image.open(path).resize((preview_width, preview_height), Image.NEAREST)
        ctk_img = customtkinter.CTkImage(
            light_image=img, dark_image=img,
            size=(preview_width, preview_height),
        )
        self._preview_images.append(ctk_img)

        is_convertible_64x64 = is_skin_size_64x64(path)
        is_valid_size = is_valid_skin_size(path) or is_convertible_64x64

        card_index = len(self._preview_cards)
        col = card_index % self._preview_cols
        row = card_index // self._preview_cols

        card = customtkinter.CTkFrame(self.preview_scroll)
        card.grid(row=row, column=col, padx=8, pady=8)

        customtkinter.CTkLabel(card, image=ctk_img, text="").grid(
            row=0, column=0, padx=8, pady=(8, 4)
        )
        customtkinter.CTkLabel(
            card, text=os.path.basename(path),
            font=customtkinter.CTkFont(size=11), wraplength=130
        ).grid(row=1, column=0, padx=8, pady=(0, 4))

        size_text = f"{image_size[0]}x{image_size[1]}"
        if is_convertible_64x64:
            size_text += " - will auto-convert to 64x32"
        if not is_valid_size:
            size_text += " - expected 64x32 or 2:1"

        customtkinter.CTkLabel(
            card, text=size_text, font=customtkinter.CTkFont(size=11),
            text_color=("#0F766E", "#5EEAD4") if is_valid_size else ("#B45309", "#FBBF24")
        ).grid(row=2, column=0, padx=8, pady=(0, 4))

        customtkinter.CTkButton(
            card, text="Remove", width=80, height=24,
            fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
            border_width=1, text_color=BUTTON_TEXT_COLOR,
            font=customtkinter.CTkFont(size=11),
            command=lambda idx=card_index: self._remove_skin_card(idx)
        ).grid(row=3, column=0, padx=8, pady=(0, 8))

        self._preview_cards.append({
            "card": card, "image": ctk_img, "path": path, "valid_size": is_valid_size,
        })

    def _remove_skin_card(self, index: int) -> None:
        if index >= len(self._preview_cards):
            return
        self._preview_cards[index]["card"].destroy()
        self._preview_cards[index] = None
        live = [c for c in self._preview_cards if c is not None]
        for i, entry in enumerate(live):
            entry["card"].grid(
                row=i // self._preview_cols, column=i % self._preview_cols, padx=8, pady=8
            )
        self._preview_cards = live
        self._preview_images = [c["image"] for c in live]

    def clear_skin_files(self) -> None:
        for widget in self.preview_scroll.winfo_children():
            widget.destroy()
        self._preview_images.clear()
        self._preview_cards.clear()

    def import_skin_files(self) -> None:
        if not self._prefix_path:
            show_error("No Prefix", "Proton prefix not set. Go to Settings to fix this.")
            return
        if not self._preview_cards:
            show_info("Import", "No skin files selected.")
            return
        invalid = [os.path.basename(e["path"]) for e in self._preview_cards if not e["valid_size"]]
        if invalid:
            show_error(
                "Invalid Skin Size",
                "These files are not 64x32 or another 2:1 size:\n\n" + "\n".join(invalid)
            )
            return

        image_paths = [e["path"] for e in self._preview_cards]
        skin_names_list = [os.path.basename(p) for p in image_paths]

        imported_ids = []
        try:
            for path, skin_name in zip(image_paths, skin_names_list):
                skin_b64 = get_import_ready_skin_base64(path)
                skin_id = append_skin(skin_b64, skin_name)
                imported_ids.append(skin_id)
        except OSError as error:
            show_error("Import Failed", f"Could not write skins to the registry.\n\n{error}")
            return
        except ValueError as error:
            show_error("Import Failed", str(error))
            return

        show_info(
            "Import Complete",
            f"Added {len(imported_ids)} skin(s).\n\n"
            + "\n".join(skin_names_list)
            + "\n\nOpen the game, edit and save the skin to persist it to the cloud."
        )

    def _on_preview_scroll_resize(self, event: tkinter.Event) -> None:
        cols = max(1, event.width // 160 - 1)
        if cols != self._preview_cols:
            self._preview_cols = cols
            self._relayout_preview_cards()

    def _relayout_preview_cards(self) -> None:
        for i in range(20):
            self.preview_scroll.grid_columnconfigure(i, weight=0)
        for i in range(self._preview_cols):
            self.preview_scroll.grid_columnconfigure(i, weight=1)
        for i, entry in enumerate(self._preview_cards):
            entry["card"].grid(
                row=i // self._preview_cols, column=i % self._preview_cols, padx=8, pady=8
            )

    # -----------------------------------------------------------------------
    # Tab 2 logic
    # -----------------------------------------------------------------------

    def refresh_manage_skins(self) -> None:
        if not self._prefix_path:
            self.manage_status_label.configure(
                text="No Proton prefix set — go to Settings."
            )
            return
        try:
            skins = get_added_skins()
        except OSError as error:
            self.manage_status_label.configure(text=f"Could not read registry: {error}")
            self._manage_skins_data = []
            for widget in self.manage_scroll.winfo_children():
                widget.destroy()
            self._manage_skin_images.clear()
            return

        self._manage_skins_data = skins
        self._populate_manage_skins(skins)

    def _populate_manage_skins(self, skins: list[dict]) -> None:
        for widget in self.manage_scroll.winfo_children():
            widget.destroy()
        self._manage_skin_images.clear()

        if not skins:
            self.manage_status_label.configure(text="No added skins found.")
            return

        self.manage_status_label.configure(text=f"Found {len(skins)} skin(s).")

        cols = self._manage_cols
        for i in range(20):
            self.manage_scroll.grid_columnconfigure(i, weight=0)
        for i in range(cols):
            self.manage_scroll.grid_columnconfigure(i, weight=1)

        for idx, skin in enumerate(skins):
            col = idx % cols
            row = idx // cols

            card = customtkinter.CTkFrame(self.manage_scroll)
            card.grid(row=row, column=col, padx=8, pady=6, sticky="n")
            card.grid_columnconfigure(1, weight=1)

            skin_b64 = skin.get("skin", "")
            if skin_b64:
                try:
                    img_bytes = base64.b64decode(skin_b64)
                    pil_img = Image.open(io.BytesIO(img_bytes)).resize((128, 64), Image.NEAREST)
                    ctk_img = customtkinter.CTkImage(
                        light_image=pil_img, dark_image=pil_img, size=(128, 64)
                    )
                    self._manage_skin_images.append(ctk_img)
                    customtkinter.CTkLabel(card, image=ctk_img, text="").grid(
                        row=0, column=0, rowspan=2, padx=(8, 10), pady=8
                    )
                except Exception:
                    customtkinter.CTkLabel(card, text="(preview unavailable)").grid(
                        row=0, column=0, rowspan=2, padx=(8, 10), pady=8
                    )

            customtkinter.CTkLabel(
                card,
                text=f"{skin.get('name', 'Unknown')}\nID: {skin.get('id', '?')}",
                anchor="w", justify="left"
            ).grid(row=0, column=1, padx=(0, 8), pady=(8, 4), sticky="w")

            customtkinter.CTkButton(
                card, text="Delete", width=90, height=28,
                fg_color=BUTTON_FG_COLOR, hover_color=BUTTON_HOVER_COLOR,
                text_color=BUTTON_TEXT_COLOR,
                command=lambda sid=skin.get("id", ""), n=skin.get("name", "Unknown"):
                    self._delete_managed_skin(sid, n)
            ).grid(row=1, column=1, padx=(0, 8), pady=(0, 8), sticky="w")

        _bind_mousewheel(self.manage_scroll)

    def _on_manage_scroll_resize(self, event: tkinter.Event) -> None:
        cols = max(1, event.width // 250 - 1)
        if cols != self._manage_cols:
            self._manage_cols = cols
            if self._manage_skins_data:
                self._populate_manage_skins(self._manage_skins_data)

    def _delete_managed_skin(self, skin_id: str, skin_name: str) -> None:
        if not skin_id:
            return
        try:
            delete_skin(skin_id)
        except OSError as error:
            show_error("Error", f"Could not delete skin from registry.\n\n{error}")
            return
        self._show_notification(f"Deleted '{skin_name}' (ID {skin_id}).")
        self.refresh_manage_skins()

    # -----------------------------------------------------------------------
    # Settings logic
    # -----------------------------------------------------------------------

    def clear_modded_skins(self) -> None:
        if not self._prefix_path:
            show_error("No Prefix", "Proton prefix not set. Go to Settings to fix this.")
            return
        confirmed = tkinter.messagebox.askyesno(
            "Clear Modded Skins",
            "This will delete User Skins, User Name Skins, and Current Equipped Skin "
            "from the registry.\n\nContinue?"
        )
        if not confirmed:
            return
        try:
            clear_modded_skins()
        except OSError as error:
            show_error("Error", f"Could not clear registry keys.\n\n{error}")
            return
        show_info("Done", "Modded skins have been cleared from the registry.")

    # -----------------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------------

    def _show_notification(self, message: str, duration_ms: int = 3000) -> None:
        if self._notification_after_id is not None:
            self.after_cancel(self._notification_after_id)
            self._notification_after_id = None
        self.notification_label.configure(text=message)
        self.notification_label.place(relx=0.99, rely=0.98, anchor="se")

        def hide():
            self.notification_label.place_forget()
            self._notification_after_id = None

        self._notification_after_id = self.after(duration_ms, hide)

    def select_tab(self, index: int) -> None:
        if self.current_tab is not None:
            self.tab_frames[self.current_tab].grid_remove()
        self.current_tab = index
        self.tab_frames[index].grid()
        if index == 2:
            self.refresh_manage_skins()

    def change_appearance_mode_event(self, new_appearance_mode: str) -> None:
        customtkinter.set_appearance_mode(new_appearance_mode)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(f"Starting Skin Importer v{VERSION}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")

    try:
        log_message("INFO", "APP", "Initializing application...")
        app = App()
        log_message("INFO", "APP", "Application initialized successfully")
        app.mainloop()
    except Exception as e:
        import traceback
        print("\n" + "=" * 60)
        print("ERROR - Application crashed!")
        print("=" * 60)
        print(f"Reason: {e}")
        print("-" * 60)
        traceback.print_exc()
        print("=" * 60)
        input("Press Enter to close…")
