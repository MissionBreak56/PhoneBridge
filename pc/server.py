#!/usr/bin/env python3
"""PhoneBridge - lado PC (Windows e Linux).

Interface gráfica do servidor. A lógica de rede está em engine.py, o protocolo em
protocol.py e o áudio em audio_backend.py. Protocolo documentado em ../PROTOCOL.md.

Uso:  server.py [--tray]    (--tray: inicia escondido na bandeja e já liga o servidor)
"""
import json
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

try:
    import pystray
except Exception:  # noqa: BLE001 - alguns desktops Linux não têm backend de bandeja
    os.environ["PYSTRAY_BACKEND"] = "xorg"
    try:
        import importlib

        pystray = importlib.import_module("pystray")
    except Exception:  # noqa: BLE001
        pystray = None

import diagnostics
import protocol as pr
import recorder
import security as sec
import sysutil
import updater
from audio_backend import IS_WIN, create_backend
from engine import BridgeServer
from version import APP_VERSION
DEFAULT_PORT = pr.DEFAULT_PORT
SINGLE_PORT = 47653  # trava local para permitir só uma janela aberta


def acquire_single_instance():
    """Retorna um socket-trava se esta é a única instância; senão avisa a
    janela já aberta (para ir à frente) e retorna None."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_PORT))
        lock.listen(1)
        return lock
    except OSError:
        lock.close()
        try:
            with socket.create_connection(("127.0.0.1", SINGLE_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return None


# ---------------------------------------------------------------- interface
BG = "#0f1115"
SURFACE = "#181b21"
BORDER = "#262a33"
TEXT = "#e6e8ec"
MUTED_TXT = "#8b919c"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6b9fff"
DANGER = "#ef5350"
OK = "#3ddc84"
FONT = "Segoe UI" if IS_WIN else "DejaVu Sans"

if IS_WIN:
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PhoneBridge")
else:
    CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "phonebridge")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def make_icon(size=64, muted=False):
    """Ícone minimalista: quadrado escuro arredondado com um microfone."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 4, fill=(24, 27, 33, 255))
    col = (239, 83, 80, 255) if muted else (79, 140, 255, 255)
    w = s // 14
    d.rounded_rectangle((s * 0.38, s * 0.16, s * 0.62, s * 0.56), radius=s * 0.12, fill=col)
    d.arc((s * 0.27, s * 0.30, s * 0.73, s * 0.72), 0, 180, fill=col, width=w)
    d.line((s * 0.5, s * 0.72, s * 0.5, s * 0.84), fill=col, width=w)
    d.line((s * 0.38, s * 0.84, s * 0.62, s * 0.84), fill=col, width=w)
    if muted:
        d.line((s * 0.2, s * 0.2, s * 0.8, s * 0.8), fill=(255, 255, 255, 255), width=w)
    return img.resize((size, size), Image.LANCZOS)


def make_qr_image(data, size=300):
    import qrcode

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    return img.resize((size, size), Image.NEAREST)


class FlatButton(tk.Label):
    def __init__(self, master, text, command, bg=ACCENT, hover=ACCENT_HOVER, fg="#ffffff"):
        super().__init__(master, text=text, bg=bg, fg=fg, font=(FONT, 11, "bold"),
                         cursor="hand2", pady=10)
        self.command, self.base, self.hover = command, bg, hover
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: super(FlatButton, self).config(bg=self.hover))
        self.bind("<Leave>", lambda e: super(FlatButton, self).config(bg=self.base))

    def set(self, text, bg, hover, fg="#ffffff"):
        self.base, self.hover = bg, hover
        super().config(text=text, bg=bg, fg=fg)


class App:
    def __init__(self, root, lock=None, start_hidden=False):
        self.root = root
        self.lock_sock = lock
        self.cfg = load_config()
        self.backend = create_backend()
        self.server = None
        self.muted = False
        self.log_q = queue.Queue()
        self.ui_q = queue.Queue()
        self.devices = []
        self.tray = None
        self.hint_shown = False
        self.level_disp = 0.0
        self.net_busy = False
        self.qr_win = None
        self.asked_this_session = False
        self.fw_unknown_logged = False
        self.registry = sec.DeviceRegistry(os.path.join(CONFIG_DIR, "devices.json"))
        self.limiter = sec.AuthLimiter()
        self.devices_win = None

        root.title("PhoneBridge")
        self._win_job = None
        self._restore_window()
        root.minsize(400, 680)
        root.configure(bg=BG)
        self.win_icon = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, self.win_icon)
        self._style()

        pad = 20
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=pad, pady=(pad, 12))

        tk.Label(wrap, text="PhoneBridge", bg=BG, fg=TEXT, font=(FONT, 22, "bold")).pack(anchor="w")
        row = tk.Frame(wrap, bg=BG)
        row.pack(anchor="w", pady=(2, 14))
        self.dot = tk.Canvas(row, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED_TXT, outline="")
        self.status = tk.Label(row, text="Servidor parado", bg=BG, fg=MUTED_TXT, font=(FONT, 10))
        self.status.pack(side="left")

        self._label(wrap, "PORTA")
        self.port_var = tk.StringVar(value=str(self.cfg.get("port", DEFAULT_PORT)))
        ttk.Entry(wrap, textvariable=self.port_var, style="D.TEntry").pack(fill="x", pady=(4, 10), ipady=4)

        self._label(wrap, "SAÍDA DO MICROFONE" + (" (EX.: CABLE INPUT)" if IS_WIN else " (PHONEBRIDGE MIC)"))
        self.dev_var = tk.StringVar()
        self.combo = ttk.Combobox(wrap, textvariable=self.dev_var, state="readonly", style="D.TCombobox")
        self.combo.pack(fill="x", pady=(4, 6), ipady=4)
        self.cable_lbl = tk.Label(wrap, text="", bg=BG, fg=MUTED_TXT, font=(FONT, 9),
                                  justify="left", wraplength=390)
        self.cable_lbl.pack(anchor="w")
        tools = tk.Frame(wrap, bg=BG)
        tools.pack(anchor="w", pady=(6, 10))
        buttons = []
        if IS_WIN:
            buttons.append(("Instalar VB-Cable", self.install_cable))
        buttons += [("Atualizar", self.refresh_devices), ("Config. de som", self.open_sound)]
        for txt, cmd in buttons:
            b = FlatButton(tools, txt, cmd, bg=SURFACE, hover=BORDER, fg=TEXT)
            b.config(font=(FONT, 9), pady=5, padx=10)
            b.pack(side="left", padx=(0, 6))
        if IS_WIN:
            self.backend_ready = True
        else:
            self.backend.prepare()
        self.refresh_devices()

        self._label(wrap, "IP DESTE PC")
        ips = pr.local_ips()
        tk.Label(wrap, text="   ".join(ips) or "indisponível", bg=BG, fg=TEXT,
                 font=(FONT, 11), justify="left", wraplength=360).pack(anchor="w", pady=(4, 6))
        pair_row = tk.Frame(wrap, bg=BG)
        pair_row.pack(fill="x", pady=(0, 12))
        for txt, cmd in (("Parear celular (QR code)", self.show_pairing), ("Celulares autorizados", self.show_devices)):
            b = FlatButton(pair_row, txt, cmd, bg=SURFACE, hover=BORDER, fg=TEXT)
            b.config(font=(FONT, 9), pady=6)
            b.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn = FlatButton(wrap, "Iniciar servidor", self.toggle)
        self.btn.pack(fill="x")
        self.mute_btn = FlatButton(wrap, "Silenciar microfone", self.toggle_mute,
                                   bg=SURFACE, hover=BORDER, fg=TEXT)
        self.mute_btn.pack(fill="x", pady=(10, 12))

        self._label(wrap, "NÍVEL DO ÁUDIO")
        self.meter = tk.Canvas(wrap, height=12, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        self.meter.pack(fill="x", pady=(4, 12))
        self.meter_bar = self.meter.create_rectangle(0, 0, 0, 12, fill=OK, outline="")

        self.opus_var = tk.BooleanVar(value=bool(self.cfg.get("opus", True)))
        self.auto_var = tk.BooleanVar(value=sysutil.is_autostart())
        self.legacy_var = tk.BooleanVar(value=bool(self.cfg.get("allow_legacy", False)))
        self.record_var = tk.BooleanVar(value=False)  # gravação sempre começa desligada
        self.disc_var = tk.BooleanVar(value=bool(self.cfg.get("discovery", True)))
        self.upd_var = tk.BooleanVar(value=bool(self.cfg.get("check_updates", True)))
        self.pairreq_var = tk.BooleanVar(value=bool(self.cfg.get("pairing_requests", True)))
        more = tk.Menubutton(wrap, text="Mais opções  ▾", bg=SURFACE, fg=TEXT, activebackground=BORDER,
                             activeforeground=TEXT, relief="flat", font=(FONT, 9), pady=6, cursor="hand2")
        menu = tk.Menu(more, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=ACCENT, activeforeground="#ffffff")
        menu.add_checkbutton(label="Permitir compressão Opus (se o celular pedir)", variable=self.opus_var,
                             command=self._opts_changed)
        menu.add_checkbutton(label="Iniciar com o Windows" if IS_WIN else "Iniciar com o sistema",
                             variable=self.auto_var, command=self._autostart_changed)
        menu.add_checkbutton(label="Gravar o áudio em WAV (Documentos/PhoneBridge)", variable=self.record_var,
                             command=self._opts_changed)
        menu.add_checkbutton(label="Responder à busca automática do celular (reiniciar o servidor)",
                             variable=self.disc_var, command=self._opts_changed)
        menu.add_checkbutton(label="Verificar atualizações ao abrir", variable=self.upd_var,
                             command=self._opts_changed)
        menu.add_checkbutton(label="Aceitar pedidos de pareamento do celular (pede a sua confirmação)",
                             variable=self.pairreq_var, command=self._opts_changed)
        menu.add_separator()
        menu.add_checkbutton(label="Aceitar apps antigos SEM proteção (inseguro)", variable=self.legacy_var,
                             command=self._legacy_toggled)
        menu.add_separator()
        menu.add_command(label="Verificar atualização agora", command=lambda: self.check_updates(manual=True))
        menu.add_command(label="Diagnóstico de conexão...", command=self.show_diagnostics)
        menu.add_command(label="Abrir pasta das gravações", command=self.open_recordings)
        more.config(menu=menu)
        more.pack(fill="x", pady=(0, 10))

        self._label(wrap, "REGISTRO")
        box = tk.Frame(wrap, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.logbox = tk.Text(box, height=6, bg=SURFACE, fg=MUTED_TXT, relief="flat", bd=0,
                              font=("Consolas" if IS_WIN else "DejaVu Sans Mono", 9), state="disabled",
                              wrap="word", padx=8, pady=6, insertbackground=TEXT)
        self.logbox.pack(fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Configure>", self._on_configure)
        self._start_tray()
        self.poll()
        if IS_WIN:
            root.after(1500, self._net_check_async)
            root.after(60000, self._net_watch)
        if updater.frozen() and self.cfg.get("check_updates", True):
            root.after(4000, self.check_updates)
        if lock is not None:
            threading.Thread(target=self._listen_show, args=(lock,), daemon=True).start()
        if start_hidden:
            root.withdraw()
            root.after(600, self.toggle)  # liga o servidor sozinho ao iniciar com o sistema

    # ---- pequenos helpers de interface
    def _check(self, parent, text, var, cmd):
        tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=BG, fg=TEXT, selectcolor=SURFACE,
                       activebackground=BG, activeforeground=TEXT, font=(FONT, 9), anchor="w",
                       highlightthickness=0, bd=0).pack(anchor="w")

    def _label(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED_TXT, font=(FONT, 8, "bold")).pack(anchor="w")

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("D.TEntry", fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT, padding=4)
        st.configure("D.TCombobox", fieldbackground=SURFACE, background=SURFACE, foreground=TEXT,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED_TXT,
                     selectbackground=SURFACE, selectforeground=TEXT, padding=4)
        st.map("D.TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _listen_show(self, lock):
        while True:
            try:
                conn, _ = lock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(8)
                except OSError:
                    pass
            self.ui_q.put("show")  # traz a janela existente à frente

    # ---- dispositivos
    def refresh_devices(self):
        try:
            self.devices = self.backend.list_output_devices()
        except Exception as e:  # noqa: BLE001
            self.devices = []
            self.log(f"Não foi possível listar os dispositivos de áudio: {e}")
        names = [n for _, n in self.devices]
        self.combo["values"] = names
        if self.dev_var.get() not in names:
            saved = self.cfg.get("device")
            pref = self.backend.preferred_device(self.devices)
            pref_name = next((n for i, n in self.devices if i == pref), None)
            self.dev_var.set(saved if saved in names else (pref_name or (names[0] if names else "")))
        self.cable_lbl.config(text=self.backend.virtual_mic_hint())

    def open_sound(self):
        try:
            if IS_WIN:
                os.startfile("ms-settings:sound")  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["pavucontrol"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            self.log("Não foi possível abrir as configurações de som")

    # ---- VB-Cable guiado (Windows)
    def install_cable(self):
        if self.backend.virtual_mic_ready():
            messagebox.showinfo("PhoneBridge", "O VB-Cable já está instalado. Se não aparecer na lista, "
                                "clique em Atualizar (ou reinicie o Windows).", parent=self.root)
            return
        if not messagebox.askyesno(
            "PhoneBridge - VB-Cable",
            "O VB-Cable é um driver de áudio virtual gratuito (vb-audio.com) que faz o PC enxergar o "
            "celular como um microfone.\n\nO PhoneBridge vai baixar o pacote oficial e abrir o instalador. "
            "Você só precisa:\n\n1. Confirmar a permissão de administrador\n2. Clicar em \"Install Driver\"\n"
            "3. Reiniciar o Windows\n4. Voltar aqui e clicar em Atualizar\n\nContinuar?",
            parent=self.root,
        ):
            return

        def work():
            res = sysutil.install_vbcable(self.log)
            if res != "ok":
                self.log("VB-Cable: abrindo a página oficial para baixar manualmente.")
                webbrowser.open(sysutil.VBCABLE_PAGE)
            else:
                self.log("VB-Cable: depois de instalar, reinicie o Windows e clique em Atualizar.")

        threading.Thread(target=work, daemon=True).start()

    # ---- pareamento (QR code) e celulares autorizados
    def show_pairing(self):
        ip = pr.primary_ip()
        if not ip:
            messagebox.showwarning("PhoneBridge", "Não foi possível descobrir o IP deste PC.", parent=self.root)
            return
        try:
            pid, psk = self.registry.create_pairing()
        except ValueError as e:
            messagebox.showwarning("PhoneBridge", str(e), parent=self.root)
            return
        port = self._cur_port()
        link = sec.pairing_link(ip, port, self.registry.pcid, pid, psk, socket.gethostname())
        if self.qr_win is not None:
            try:
                self.qr_win.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        win.title("PhoneBridge - parear celular")
        win.configure(bg=BG)
        win.resizable(False, False)
        self.qr_photo = ImageTk.PhotoImage(make_qr_image(link, 320))
        tk.Label(win, image=self.qr_photo, bg="white", bd=0).pack(padx=24, pady=(24, 10))
        tk.Label(win, text=f"{ip}:{port}", bg=BG, fg=TEXT, font=(FONT, 12, "bold")).pack()
        tk.Label(win, text="No celular: PhoneBridge > QR code.\nEste código contém a chave de pareamento: mostre só "
                 "para o seu celular.\nVale por 10 minutos e só pode ser usado por um aparelho.",
                 bg=BG, fg=MUTED_TXT, font=(FONT, 9), justify="center").pack(pady=(4, 8))

        def copy():
            win.clipboard_clear()
            win.clipboard_append(link)
            self.log("Link de pareamento copiado (vale por 10 minutos).")

        def close():
            win.destroy()  # o código continua valendo até expirar (10 min): o celular pode ainda estar conectando

        b = FlatButton(win, "Copiar link de pareamento", copy, bg=SURFACE, hover=BORDER, fg=TEXT)
        b.config(font=(FONT, 9), pady=6)
        b.pack(padx=24, pady=(0, 20), fill="x")
        win.protocol("WM_DELETE_WINDOW", close)
        self.qr_win = win
        self.log("Pareamento criado: leia o QR code no celular em até 10 minutos.")

    # ---- pedido de pareamento vindo do celular (sem QR): o usuário confere o código e aprova
    def _pair_handler(self, name, ip, code, holder):
        """Chamado pela thread do servidor; a pergunta aparece na interface."""
        self.ui_q.put(("pair_request", name, ip, code, holder))

    def _show_pair_request(self, name, ip, code, holder):
        if holder.event.is_set():
            return
        self.show()
        if self.tray:
            try:
                self.tray.notify(f"{name} quer parear. Confira o código.", "PhoneBridge")
            except Exception:  # noqa: BLE001
                pass
        win = tk.Toplevel(self.root)
        win.title("PhoneBridge - pedido de pareamento")
        win.configure(bg=BG)
        win.resizable(False, False)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(win, text="Um celular quer parear com este PC", bg=BG, fg=TEXT, font=(FONT, 13, "bold")).pack(padx=24, pady=(20, 4))
        tk.Label(win, text=f"{name}\n{ip}", bg=BG, fg=MUTED_TXT, font=(FONT, 10)).pack()
        tk.Label(win, text=f"{code[:3]} {code[3:]}", bg=BG, fg=ACCENT, font=(FONT, 36, "bold")).pack(pady=(10, 4))
        tk.Label(win, text="Confira se este código é IGUAL ao que aparece na tela do celular.\nSe for diferente, ou se você não "
                 "está pareando agora, clique em Recusar.", bg=BG, fg=TEXT, font=(FONT, 9), justify="center",
                 wraplength=380).pack(padx=24)
        left = tk.Label(win, text="", bg=BG, fg=MUTED_TXT, font=(FONT, 9))
        left.pack(pady=(8, 4))
        state = {"remaining": int(sec.PAIR_TIMEOUT)}

        def decide(ok):
            if not holder.event.is_set():
                holder.answer(ok)
            try:
                win.destroy()
            except tk.TclError:
                pass

        row = tk.Frame(win, bg=BG)
        row.pack(padx=24, pady=(4, 20), fill="x")
        allow = FlatButton(row, "Permitir", lambda: decide(True))
        allow.pack(side="left", fill="x", expand=True, padx=(0, 6))
        deny = FlatButton(row, "Recusar", lambda: decide(False), bg=SURFACE, hover=BORDER, fg=TEXT)
        deny.pack(side="left", fill="x", expand=True)
        win.protocol("WM_DELETE_WINDOW", lambda: decide(False))

        def tick():
            if holder.event.is_set():  # respondido, expirou ou o celular desistiu
                try:
                    win.destroy()
                except tk.TclError:
                    pass
                return
            state["remaining"] -= 1
            if state["remaining"] <= 0:
                decide(False)
                return
            left.config(text=f"Fecha sozinho em {state['remaining']} s")
            win.after(1000, tick)

        left.config(text=f"Fecha sozinho em {state['remaining']} s")
        win.after(1000, tick)

    def show_devices(self):
        if self.devices_win is not None:
            try:
                self.devices_win.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        win.title("Celulares autorizados")
        win.configure(bg=BG)
        win.geometry("460x320")
        self.devices_win = win
        tk.Label(win, text="Celulares autorizados", bg=BG, fg=TEXT, font=(FONT, 13, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="Só eles conseguem conectar. Remover desfaz o pareamento na hora.", bg=BG, fg=MUTED_TXT,
                 font=(FONT, 9)).pack(anchor="w", padx=16, pady=(0, 8))
        box = tk.Listbox(win, bg=SURFACE, fg=TEXT, selectbackground=ACCENT, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER, font=(FONT, 10), activestyle="none")
        box.pack(fill="both", expand=True, padx=16)
        rows = []

        def refresh():
            box.delete(0, "end")
            rows.clear()
            for d in self.registry.list_devices():
                seen = time.strftime("%d/%m %H:%M", time.localtime(d["last_seen"])) if d["last_seen"] else "nunca"
                label = (d["name"] or "(aguardando a primeira conexão)") if d["active"] else "(pareamento pendente)"
                box.insert("end", f"{label}  -  {d['last_ip'] or '-'}  -  visto: {seen}")
                rows.append(d["pid"])
            if not rows:
                box.insert("end", "Nenhum celular pareado. Use \"Parear celular (QR code)\".")

        def remove():
            sel = box.curselection()
            if not sel or sel[0] >= len(rows):
                return
            pid = rows[sel[0]]
            if messagebox.askyesno("PhoneBridge", "Remover este celular? Ele precisará ser pareado de novo.", parent=win):
                self.registry.revoke(pid)
                if self.server:
                    self.server.disconnect_device(pid)
                self.log("Celular removido da lista de autorizados.")
                refresh()

        refresh()
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=16, pady=12)
        for txt, cmd in (("Remover selecionado", remove), ("Atualizar lista", refresh)):
            b = FlatButton(row, txt, cmd, bg=SURFACE, hover=BORDER, fg=TEXT)
            b.config(font=(FONT, 9), pady=6)
            b.pack(side="left", padx=(0, 6))

    def show_diagnostics(self):
        text = diagnostics.build_report(self._cur_port(), self.backend, self.server, self.registry)
        win = tk.Toplevel(self.root)
        win.title("Diagnóstico")
        win.configure(bg=BG)
        win.geometry("520x380")
        box = tk.Text(win, bg=SURFACE, fg=TEXT, relief="flat", font=("Consolas" if IS_WIN else "DejaVu Sans Mono", 9),
                      wrap="word", padx=10, pady=8)
        box.insert("1.0", text)
        box.config(state="disabled")
        box.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        def copy():
            win.clipboard_clear()
            win.clipboard_append(text)

        b = FlatButton(win, "Copiar", copy, bg=SURFACE, hover=BORDER, fg=TEXT)
        b.config(font=(FONT, 9), pady=6)
        b.pack(padx=12, pady=(0, 12), anchor="e")

    def open_recordings(self):
        d = self.cfg.get("record_dir") or recorder.default_dir()
        os.makedirs(d, exist_ok=True)
        try:
            if IS_WIN:
                os.startfile(d)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", d], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            self.log(f"Gravações em: {d}")

    # ---- bandeja
    def _start_tray(self):
        if pystray is None:
            self.tray = None
            self.log("Bandeja do sistema indisponível: o programa fica só na janela.")
            return
        items = [
            pystray.MenuItem("Abrir PhoneBridge", lambda: self.ui_q.put("show"), default=True),
            pystray.MenuItem(lambda item: "Reativar microfone" if self.muted else "Silenciar microfone",
                             lambda: self.ui_q.put("mute")),
            pystray.MenuItem(lambda item: "Parar servidor" if self.server else "Iniciar servidor",
                             lambda: self.ui_q.put("toggle")),
            pystray.MenuItem("Parear celular (QR code)", lambda: self.ui_q.put("qr")),
        ]
        if IS_WIN:
            items.append(pystray.MenuItem("Firewall e redes (opcional)", lambda: self.ui_q.put("firewall")))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Sair", lambda: self.ui_q.put("quit"))]
        self.tray = pystray.Icon("PhoneBridge", make_icon(64), "PhoneBridge", pystray.Menu(*items))
        try:
            self.tray.run_detached()
        except Exception as e:  # noqa: BLE001 - sem bandeja disponível (alguns desktops Linux)
            self.tray = None
            self.log(f"Bandeja do sistema indisponível: {e}")

    # ---- lembrar posição/tamanho da janela
    def _virtual_screen(self):
        try:
            import ctypes

            u = ctypes.windll.user32
            return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        except Exception:  # noqa: BLE001
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _restore_window(self):
        geo = "440x720"
        w = self.cfg.get("window")
        if isinstance(w, dict):
            try:
                vx, vy, vw, vh = self._virtual_screen()
                ww = max(400, min(int(w["w"]), vw))
                hh = max(680, min(int(w["h"]), vh))
                x = max(vx, min(int(w["x"]), vx + vw - ww))  # garante que fique dentro de alguma tela
                y = max(vy, min(int(w["y"]), vy + vh - hh))
                geo = f"{ww}x{hh}+{x}+{y}"
            except (KeyError, TypeError, ValueError):
                pass
        self.root.geometry(geo)
        if self.cfg.get("zoomed"):
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _on_configure(self, e):
        if e.widget is not self.root:
            return
        if self._win_job:
            self.root.after_cancel(self._win_job)
        self._win_job = self.root.after(600, self._save_window)

    def _save_window(self):
        self._win_job = None
        try:
            state = self.root.state()
        except tk.TclError:
            return
        if state == "normal":
            self.cfg["window"] = {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                                  "w": self.root.winfo_width(), "h": self.root.winfo_height()}
            self.cfg["zoomed"] = False
        elif state == "zoomed":
            self.cfg["zoomed"] = True
        else:
            return
        save_config(self.cfg)

    # ---- opções
    def _record_dir(self):
        return (self.cfg.get("record_dir") or recorder.default_dir()) if self.record_var.get() else None

    def _opts_changed(self):
        self.cfg.update(opus=bool(self.opus_var.get()), discovery=bool(self.disc_var.get()),
                        check_updates=bool(self.upd_var.get()), pairing_requests=bool(self.pairreq_var.get()))
        save_config(self.cfg)
        if self.server:
            self.server.opus_allowed = self.cfg["opus"]
            self.server.pairing_enabled = self.cfg["pairing_requests"]
            self.server.record_dir = self._record_dir()  # vale para as próximas conexões
        if self.record_var.get():
            self.log(f"Gravação ligada: vale para as próximas conexões ({self._record_dir()}).")

    def _legacy_toggled(self):
        if self.legacy_var.get():
            ok = messagebox.askyesno(
                "PhoneBridge - modo sem proteção",
                "Apps antigos (versão 2.x e anteriores) não usam senha nem criptografia: qualquer aparelho da rede "
                "poderia usar seu microfone ou ouvir o som do PC.\n\nLigue só em rede de confiança e desligue "
                "depois. O ideal é atualizar o app do celular.\n\nLigar mesmo assim?", icon="warning", parent=self.root)
            if not ok:
                self.legacy_var.set(False)
                return
        self.cfg["allow_legacy"] = bool(self.legacy_var.get())
        save_config(self.cfg)
        if self.server:
            self.server.allow_legacy = self.cfg["allow_legacy"]
        self.log("Modo SEM proteção LIGADO para apps antigos." if self.cfg["allow_legacy"]
                 else "Modo sem proteção desligado: só celulares pareados conectam.")

    def _autostart_changed(self):
        ok = sysutil.set_autostart(bool(self.auto_var.get()))
        if not ok:
            self.auto_var.set(sysutil.is_autostart())
            self.log("Não foi possível alterar a inicialização automática.")
        else:
            self.log("Vai iniciar com o sistema." if self.auto_var.get() else "Não vai mais iniciar com o sistema.")

    # ---- firewall e rede privada (Windows): cada alteração é opcional e pedida separadamente
    def _cur_port(self):
        try:
            return int(self.port_var.get())
        except ValueError:
            return DEFAULT_PORT

    def _net_check_async(self, force=False):
        if not IS_WIN or self.net_busy:
            return
        port = self._cur_port()

        def work():
            state = sysutil.fw_rules_state(port)
            pubs = sysutil.public_networks()
            if force:
                self.ui_q.put(("ask_net", port, state, pubs, True))
                return
            if state == "unknown" and not self.fw_unknown_logged:
                self.fw_unknown_logged = True
                self.log("Não foi possível verificar as regras do Firewall. Se o celular não conectar, use o menu da "
                         "bandeja > Liberar no Firewall.")
            need_fw = state == "missing" and port not in self.cfg.get("fw_declined", [])
            declined = set(self.cfg.get("net_declined", []))
            pubs = [n for n in pubs if n not in declined]
            if need_fw or pubs:
                self.ui_q.put(("ask_net", port, state, pubs, False))

        threading.Thread(target=work, daemon=True).start()

    def _net_watch(self):
        # a cada minuto vê se o PC entrou numa rede Pública nova (só pergunta uma vez por rede)
        if self.server:
            self._net_check_async()
        self.root.after(60000, self._net_watch)

    def _ask_net(self, port, state, pubs, force):
        if self.net_busy:
            return
        do_fw = do_priv = False
        if state == "missing" or (force and state != "ok"):
            do_fw = messagebox.askyesno(
                "PhoneBridge - Firewall",
                f"Para o celular conectar, o PhoneBridge pode criar duas regras de entrada no Firewall do Windows: "
                f"porta {port} (TCP, áudio) e porta {pr.DISCOVERY_PORT} (UDP, busca automática), ambas só para "
                "dispositivos da sua rede local.\n\nO Windows vai pedir permissão de administrador. Criar as regras?",
                parent=self.root)
            if not do_fw and not force:
                self.cfg.setdefault("fw_declined", []).append(port)
        if pubs:
            do_priv = messagebox.askyesno(
                "PhoneBridge - Rede",
                "Estas redes conectadas estão como \"Pública\": " + ", ".join(pubs) + ".\n\nNo Windows, redes "
                "Públicas bloqueiam a conexão do celular. Mudar para \"Privada\" deixa o PC visível nessas redes e "
                "libera mais recursos de compartilhamento: faça isso só em redes de confiança (casa, trabalho).\n\n"
                "Mudar estas redes para Privada?", icon="warning", parent=self.root)
            if not do_priv:
                nd = self.cfg.setdefault("net_declined", [])
                nd.extend(n for n in pubs if n not in nd)
        save_config(self.cfg)
        if not (do_fw or do_priv):
            if force and state == "ok" and not pubs:
                self.log("Firewall e redes já estão configurados.")
            else:
                self.log("Rede/Firewall: nada foi alterado. Use o menu da bandeja se o celular não conseguir conectar.")
            return
        if do_fw and port in self.cfg.get("fw_declined", []):
            self.cfg["fw_declined"].remove(port)
        self.net_busy = True
        self.log("Rede/Firewall: aguardando a permissão de administrador...")
        threading.Thread(target=self._net_apply, args=(port, do_fw, do_priv), daemon=True).start()

    def _net_apply(self, port, fw, priv):
        done = False
        if sysutil.setup_network_elevated(port, firewall=fw, private=priv):
            for _ in range(30):
                time.sleep(1)
                if (not fw or sysutil.fw_rules_state(port) == "ok") and (not priv or not sysutil.public_networks()):
                    done = True
                    break
        self.log("Rede/Firewall: alterações aplicadas." if done
                 else "Rede/Firewall: não foi possível concluir (permissão negada ou nada mudou).")
        self.net_busy = False

    # ---- atualizações (GitHub Releases, com assinatura e SHA-256)
    def check_updates(self, manual=False):
        def work():
            try:
                rel = updater.check()
            except updater.UpdateError as e:
                if manual:
                    self.log(f"Atualização: {e}")
                return
            self.ui_q.put(("update", rel, manual))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, rel, manual):
        if rel is None:
            if manual:
                messagebox.showinfo("PhoneBridge", f"Você já está na versão mais recente ({APP_VERSION}).",
                                    parent=self.root)
            return
        asset = updater.pick_asset(rel)
        notes = rel["notes"].strip()[:700]
        msg = f"Nova versão disponível: {rel['tag']} (instalada: {APP_VERSION}).\n\n{notes}\n\n"
        if asset is None or not updater.UPDATE_PUBLIC_KEY:
            if messagebox.askyesno("PhoneBridge - atualização", msg + "Abrir a página da versão para baixar?",
                                   parent=self.root):
                webbrowser.open(rel["url"] or f"https://github.com/{updater.REPO}/releases/latest")
            return
        if not messagebox.askyesno("PhoneBridge - atualização", msg + "Baixar e verificar agora? (a assinatura e o "
                                   "SHA-256 são conferidos antes de qualquer instalação)", parent=self.root):
            return
        self.log(f"Baixando {asset}...")

        def work():
            try:
                path = updater.download_verified(rel, asset)
            except updater.UpdateError as e:
                self.ui_q.put(("update_done", None, str(e)))
                return
            self.ui_q.put(("update_done", path, ""))

        threading.Thread(target=work, daemon=True).start()

    def _update_done(self, path, error):
        if not path:
            self.log(f"Atualização recusada: {error}")
            messagebox.showerror("PhoneBridge", f"Não foi possível atualizar:\n{error}", parent=self.root)
            return
        self.log("Atualização verificada (assinatura e SHA-256 conferem).")
        if updater.launch_installer(path):
            messagebox.showinfo("PhoneBridge", "Atualização verificada. O instalador foi aberto; o PhoneBridge "
                                "vai fechar para ser atualizado.", parent=self.root)
            self.quit()
        else:
            messagebox.showinfo("PhoneBridge", f"Atualização verificada e salva em:\n{path}\n\nExtraia e substitua "
                                "o programa.", parent=self.root)

    # ---- janela
    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray is None:
            self.quit()  # sem bandeja não há como reabrir a janela
            return
        self.root.withdraw()
        if not self.hint_shown and self.tray:
            self.hint_shown = True
            try:
                self.tray.notify("Continua em execução na bandeja do sistema.", "PhoneBridge")
            except Exception:  # noqa: BLE001
                pass

    # ---- ações
    def log(self, msg):
        self.log_q.put(msg)

    def poll(self):
        while not self.log_q.empty():
            self.logbox.config(state="normal")
            self.logbox.insert("end", self.log_q.get() + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        while not self.ui_q.empty():
            act = self.ui_q.get()
            if isinstance(act, tuple) and act[0] == "ask_net":
                self._ask_net(*act[1:])
            elif isinstance(act, tuple) and act[0] == "pair_request":
                self._show_pair_request(*act[1:])
            elif isinstance(act, tuple) and act[0] == "update":
                self._show_update(*act[1:])
            elif isinstance(act, tuple) and act[0] == "update_done":
                self._update_done(*act[1:])
            elif act == "show":
                self.show()
            elif act == "mute":
                self.toggle_mute()
            elif act == "toggle":
                self.toggle()
            elif act == "qr":
                self.show()
                self.show_pairing()
            elif act == "firewall":
                self._net_check_async(force=True)
            elif act == "quit":
                self.quit()
                return
        self.update_status()
        self.update_meter()
        self.root.after(100, self.poll)

    def update_meter(self):
        lvl = 0.0
        if self.server and time.time() - self.server.level_ts < 0.4:
            lvl = min(1.0, self.server.level * 1.6)  # um pouco de ganho visual
        self.level_disp = max(lvl, self.level_disp * 0.8)
        w = self.meter.winfo_width()
        self.meter.coords(self.meter_bar, 0, 0, w * self.level_disp, 12)
        clipping = bool(self.server) and time.time() - self.server.clip_ts < 1.0
        self.meter.itemconfig(self.meter_bar, fill=DANGER if (self.level_disp > 0.92 or clipping) else OK)

    def update_status(self):
        if not self.server:
            text, col = "Servidor parado", MUTED_TXT
        else:
            with self.server.lock:
                clients = list(self.server.clients.values())
            if clients:
                ips = ", ".join(sorted({c[0] for c in clients}))
                codecs = {("Opus" if c[2] == pr.CODEC_OPUS else "PCM") for c in clients}
                text, col = f"Conectado: {ips} ({'/'.join(sorted(codecs))})", OK
            else:
                text, col = f"Aguardando conexão na porta {self.server.port}", ACCENT
        self.status.config(text=text)
        self.dot.itemconfig(self.dot_id, fill=col)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.server:
            self.server.muted = self.muted
        if self.muted:
            self.mute_btn.set("Microfone silenciado", DANGER, "#f4726f")
        else:
            self.mute_btn.set("Silenciar microfone", SURFACE, BORDER, TEXT)
        if self.tray:
            self.tray.icon = make_icon(64, self.muted)
            self.tray.update_menu()
        self.log("Microfone silenciado" if self.muted else "Microfone reativado")

    def toggle(self):
        if self.server:
            self.server.stop()
            self.server = None
            self.btn.set("Iniciar servidor", ACCENT, ACCENT_HOVER)
            if self.tray:
                self.tray.update_menu()
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.log("Porta inválida")
            return
        idx = next((i for i, n in self.devices if n == self.dev_var.get()), None)
        try:
            self.server = BridgeServer(
                port, idx, self.log, self.backend, self.opus_var.get(), registry=self.registry,
                allow_legacy=bool(self.cfg.get("allow_legacy", False)), limiter=self.limiter,
                discovery_enabled=bool(self.disc_var.get()), record_dir=self._record_dir())
            self.server.muted = self.muted
            self.server.pair_handler = self._pair_handler
            self.server.pairing_enabled = bool(self.pairreq_var.get())
            self.server.start()
            if not self.backend.virtual_mic_ready():
                self.log("Aviso: sem microfone virtual, o modo microfone toca só na saída escolhida; "
                         "outros apps não o verão como microfone. O modo alto-falante funciona normalmente.")
            if self.opus_var.get() and not pr.opus_available():
                self.log("Opus indisponível neste PC (biblioteca não encontrada): usando PCM.")
            self.btn.set("Parar servidor", SURFACE, BORDER, TEXT)
            self.cfg.update(port=port, device=self.dev_var.get())
            save_config(self.cfg)
        except OSError as e:
            self.server = None
            self.log(f"Não foi possível abrir a porta: {e}")
        if self.tray:
            self.tray.update_menu()
        if self.server and IS_WIN:
            self._net_check_async()

    def quit(self):
        self._save_window()
        if self.server:
            self.server.stop()
        self.backend.shutdown()
        if self.tray:
            self.tray.stop()
        try:
            if self.lock_sock:
                self.lock_sock.close()
        except OSError:
            pass
        self.root.destroy()


if __name__ == "__main__":
    lock = acquire_single_instance()
    if lock is None:
        raise SystemExit(0)  # já está aberto: a janela existente foi trazida à frente
    root = tk.Tk()
    App(root, lock, start_hidden="--tray" in sys.argv)
    root.mainloop()
