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
# Só mexemos em regras com os nossos nomes exatos: "PhoneBridge <porta>" e "PhoneBridge descoberta".
FW_NAME_REGEX = r"^PhoneBridge( \d+| descoberta)$"


def _rule_state(name):
    """'yes' (existe), 'no' (confirmado que não existe) ou 'unknown' (não deu para verificar)."""
    r = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"], timeout=10)
    if r is None:
        return "unknown"
    if r.returncode == 0:
        return "yes"
    # sem regra o netsh devolve 1 e uma mensagem; qualquer outro código é falha de verificação
    return "no" if r.returncode == 1 else "unknown"


def fw_rules_state(port):
    """'ok' | 'missing' | 'unknown'. Nunca presume que a regra existe quando não consegue confirmar."""
    if not IS_WIN:
        return "ok"
    states = {_rule_state(fw_tcp_name(port)), _rule_state(FW_UDP_NAME)}
    if "unknown" in states:
        return "unknown"
    return "ok" if states == {"yes"} else "missing"


def fw_rules_ok(port):
    return fw_rules_state(port) == "ok"


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


def _setup_script(port, firewall=True, private=False):
    lines = ["$ErrorActionPreference = 'SilentlyContinue'"]
    if firewall:
        lines += [
            # remove só as regras do próprio PhoneBridge (nomes exatos) antes de recriar: evita duplicatas
            f"Get-NetFirewallRule | Where-Object {{ $_.DisplayName -match '{FW_NAME_REGEX}' }} | Remove-NetFirewallRule",
            f"New-NetFirewallRule -DisplayName '{fw_tcp_name(port)}' -Direction Inbound -Action Allow -Protocol TCP "
            f"-LocalPort {port} -RemoteAddress LocalSubnet -Profile Any | Out-Null",
            f"New-NetFirewallRule -DisplayName '{FW_UDP_NAME}' -Direction Inbound -Action Allow -Protocol UDP "
            f"-LocalPort {DISCOVERY_PORT} -RemoteAddress LocalSubnet -Profile Any | Out-Null",
        ]
    if private:
        lines.append("Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | ForEach-Object "
                     "{ Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private }")
    return "\n".join(lines) + "\n"


def setup_network_elevated(port, firewall=True, private=False):
    """Pede permissão de administrador (UAC) e aplica só o que foi autorizado: regras de firewall e/ou
    mudar as redes Públicas conectadas para Privada. Retorna True se o pedido foi aceito."""
    if not IS_WIN or not (firewall or private):
        return False
    import ctypes

    path = os.path.join(tempfile.mkdtemp(prefix="phonebridge_"), "setup.ps1")
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(_setup_script(port, firewall, private))
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
# SHA-256 do pacote oficial (VBCABLE_Driver_Pack43.zip). Sem hash conhecido não instalamos automaticamente:
# o app abre a página oficial para o usuário baixar. O CI confere se o arquivo publicado ainda bate com este hash.
VBCABLE_SHA256 = ""
VBCABLE_MAX_BYTES = 8 * 1024 * 1024


def safe_extract(zf, dest):
    """Extrai o zip recusando caminhos que saiam da pasta de destino (Zip Slip) e links simbólicos."""
    root = os.path.realpath(dest)
    for info in zf.infolist():
        target = os.path.realpath(os.path.join(root, info.filename))
        if os.path.commonpath([root, target]) != root:
            raise ValueError(f"caminho suspeito no zip: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"link simbólico no zip: {info.filename}")
    zf.extractall(root)


def install_vbcable(log):
    """Baixa o pacote oficial do VB-Audio, confere o SHA-256, extrai com segurança e abre o instalador como
    administrador. Retorna 'ok', ou 'manual' se não der para instalar automaticamente."""
    if not IS_WIN:
        return "manual"
    import ctypes
    import hashlib
    import shutil

    if not VBCABLE_SHA256:
        log("VB-Cable: instalação automática desativada (hash do pacote não configurado).")
        return "manual"
    tmp = tempfile.mkdtemp(prefix="phonebridge_vbcable_")
    try:
        log("VB-Cable: baixando o pacote oficial...")
        zpath = os.path.join(tmp, "vbcable.zip")
        req = urllib.request.Request(VBCABLE_ZIP, headers={"User-Agent": "PhoneBridge"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(VBCABLE_MAX_BYTES + 1)
        if len(data) > VBCABLE_MAX_BYTES:
            raise ValueError("pacote maior que o esperado")
        if hashlib.sha256(data).hexdigest() != VBCABLE_SHA256:
            raise ValueError("SHA-256 do pacote não confere (o arquivo mudou ou foi adulterado)")
        with open(zpath, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(zpath) as z:
            safe_extract(z, tmp)
        exe = "VBCABLE_Setup_x64.exe" if platform.machine().endswith("64") else "VBCABLE_Setup.exe"
        exe_path = os.path.join(tmp, exe)
        if not os.path.exists(exe_path):
            raise FileNotFoundError(exe)
        log("VB-Cable: abrindo o instalador (confirme a permissão de administrador)...")
        ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe_path, None, tmp, 1) > 32
        return "ok" if ok else "manual"
    except Exception as e:  # noqa: BLE001
        log(f"VB-Cable: não foi possível instalar automaticamente ({e}).")
        shutil.rmtree(tmp, ignore_errors=True)
        return "manual"
