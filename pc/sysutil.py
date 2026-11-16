"""Integração com o sistema: firewall/rede privada, iniciar com o sistema e instalação do VB-Cable."""
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from protocol import DISCOVERY_PORT

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
APP_NAME = "PhoneBridge"
FW_UDP_NAME = "PhoneBridge descoberta"
VBCABLE_PAGE = "https://vb-audio.com/Cable/"
VBCABLE_ZIP = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack43.zip"


def fw_tcp_name(port):
    return f"PhoneBridge {port}"


def _run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              creationflags=NO_WINDOW, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------ firewall e rede (Windows)
def _rule_exists(name):
    r = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"], timeout=10)
    return r is None or r.returncode == 0  # não deu para checar: melhor não incomodar


def fw_rules_ok(port):
    """True se as regras de entrada (TCP do áudio e UDP da descoberta) já existem."""
    if not IS_WIN:
        return True
    return _rule_exists(fw_tcp_name(port)) and _rule_exists(FW_UDP_NAME)


def public_networks():
    """Nomes das redes conectadas classificadas como Públicas."""
    if not IS_WIN:
        return []
    r = _run(["powershell", "-NoProfile", "-Command",
              "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } "
              "| ForEach-Object { $_.Name }"])
    if r is None or r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _setup_script(port):
    return f"""$ErrorActionPreference = 'SilentlyContinue'
Get-NetFirewallRule -DisplayName 'PhoneBridge*' | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName '{fw_tcp_name(port)}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {port} -RemoteAddress LocalSubnet -Profile Any | Out-Null
New-NetFirewallRule -DisplayName '{FW_UDP_NAME}' -Direction Inbound -Action Allow -Protocol UDP -LocalPort {DISCOVERY_PORT} -RemoteAddress LocalSubnet -Profile Any | Out-Null
Get-NetConnectionProfile | Where-Object {{ $_.NetworkCategory -eq 'Public' }} | ForEach-Object {{ Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private }}
"""


def setup_network_elevated(port):
    """Pede permissão de administrador (UAC) e: cria as regras de firewall e muda as redes Públicas
    conectadas para Privada. Retorna True se o pedido foi aceito (o script roda em segundo plano)."""
    if not IS_WIN:
        return False
    import ctypes

    path = os.path.join(tempfile.gettempdir(), "phonebridge_setup.ps1")
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(_setup_script(port))
        args = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{path}"'
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 0) > 32
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ iniciar com o sistema
def _autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}" --tray'


def _desktop_file():
    return os.path.join(os.path.expanduser("~/.config/autostart"), "phonebridge.desktop")


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_autostart():
    if IS_WIN:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False
    return os.path.exists(_desktop_file())


def set_autostart(enabled):
    try:
        if IS_WIN:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                if enabled:
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
                else:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
        else:
            path = _desktop_file()
            if enabled:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("[Desktop Entry]\nType=Application\nName=PhoneBridge\n"
                            f"Exec={_autostart_command()}\nX-GNOME-Autostart-enabled=true\n")
            elif os.path.exists(path):
                os.remove(path)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ VB-Cable (Windows)
def install_vbcable(log):
    """Baixa o pacote oficial do VB-Audio, extrai e abre o instalador como administrador.
    Retorna 'ok', ou 'manual' se o download falhar (então abra a página oficial)."""
    if not IS_WIN:
        return "manual"
    import ctypes

    try:
        log("VB-Cable: baixando o pacote oficial...")
        tmp = tempfile.mkdtemp(prefix="phonebridge_vbcable_")
        zpath = os.path.join(tmp, "vbcable.zip")
        req = urllib.request.Request(VBCABLE_ZIP, headers={"User-Agent": "PhoneBridge"})
        with urllib.request.urlopen(req, timeout=30) as r, open(zpath, "wb") as f:
            f.write(r.read())
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        exe = "VBCABLE_Setup_x64.exe" if platform.machine().endswith("64") else "VBCABLE_Setup.exe"
        exe_path = os.path.join(tmp, exe)
        if not os.path.exists(exe_path):
            raise FileNotFoundError(exe)
        log("VB-Cable: abrindo o instalador (confirme a permissão de administrador)...")
        ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe_path, None, tmp, 1) > 32
        return "ok" if ok else "manual"
    except Exception as e:  # noqa: BLE001
        log(f"VB-Cable: não foi possível baixar automaticamente ({e}).")
        return "manual"
