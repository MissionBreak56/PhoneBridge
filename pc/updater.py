"""Atualizador: consulta o GitHub Releases, baixa o pacote e só o aceita se a lista de hashes estiver
assinada pela chave do projeto e o SHA-256 do arquivo bater.

Cadeia de confiança: a chave pública de assinatura (UPDATE_PUBLIC_KEY) vai dentro do programa. O CI assina o
arquivo SHA256SUMS de cada release com a chave privada (guardada nos segredos do GitHub). O programa baixa o
SHA256SUMS + SHA256SUMS.sig, confere a assinatura Ed25519 e só então confere o SHA-256 do instalador.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from version import APP_VERSION, REPO

# Chave pública Ed25519 (32 bytes, hex) que assina o SHA256SUMS das releases. Vazia = ainda não configurada:
# nesse caso o atualizador só mostra que há versão nova e não baixa nada.
UPDATE_PUBLIC_KEY = "a098f91b3533b807113831edffa84e5b4cddf1f43605f5102059c4b28e0fdd87"

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com"}
MAX_DOWNLOAD = 250 * 1024 * 1024
MAX_SUMS = 64 * 1024
USER_AGENT = f"PhoneBridge/{APP_VERSION}"


class UpdateError(Exception):
    pass


def parse_version(text):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?", text.strip())
    if not m:
        raise ValueError(f"versão inválida: {text!r}")
    major, minor, patch, pre = m.groups()
    # versão com sufixo (-rc1) vale menos que a final
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


def is_newer(candidate, current=APP_VERSION):
    try:
        return parse_version(candidate) > parse_version(current)
    except ValueError:
        return False


def _check_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
        raise UpdateError(f"endereço não permitido: {url}")


def _open(url, timeout=20):
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    return urllib.request.urlopen(req, timeout=timeout)


def _read_limited(url, limit):
    with _open(url) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("arquivo maior que o permitido")
    return data


def check(current=APP_VERSION, fetch=None):
    """Retorna None se está em dia, ou um dict com a release mais nova."""
    try:
        raw = (fetch or (lambda u: _read_limited(u, 512 * 1024)))(API_LATEST)
        rel = json.loads(raw)
        tag = rel["tag_name"]
    except (OSError, ValueError, KeyError) as e:
        raise UpdateError(f"não foi possível consultar as versões: {e}")
    if rel.get("draft") or rel.get("prerelease") or not is_newer(tag, current):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])
              if isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str)}
    return {"tag": tag, "notes": (rel.get("body") or "")[:2000], "url": rel.get("html_url", ""), "assets": assets}


def pick_asset(release):
    if os.name == "nt":
        name = "PhoneBridgeSetup.exe"
    else:
        name = "PhoneBridgeServer-linux.tar.gz"
    return name if name in release["assets"] else None


def verify_sums(sums_bytes, sig_bytes, public_key_hex=None):
    """Confere a assinatura Ed25519 do SHA256SUMS. Levanta UpdateError se inválida."""
    key_hex = UPDATE_PUBLIC_KEY if public_key_hex is None else public_key_hex
    if not key_hex:
        raise UpdateError("este programa ainda não tem a chave de assinatura das atualizações")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(sig_bytes, sums_bytes)
    except (InvalidSignature, ValueError):
        raise UpdateError("assinatura da lista de hashes inválida: atualização recusada")


def parse_sums(sums_bytes):
    """Formato do sha256sum: '<hash>  <arquivo>' por linha."""
    result = {}
    for line in sums_bytes.decode("utf-8", "replace").splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if m:
            result[m.group(2)] = m.group(1).lower()
    return result


def download_verified(release, asset_name, dest_dir=None, progress=None, public_key_hex=None):
    """Baixa o asset e o confere. Retorna o caminho do arquivo verificado."""
    assets = release["assets"]
    for needed in (asset_name, "SHA256SUMS", "SHA256SUMS.sig"):
        if needed not in assets:
            raise UpdateError(f"a release não tem {needed}")
    sums = _read_limited(assets["SHA256SUMS"], MAX_SUMS)
    sig = _read_limited(assets["SHA256SUMS.sig"], 1024)
    verify_sums(sums, sig, public_key_hex)
    expected = parse_sums(sums).get(asset_name)
    if not expected:
        raise UpdateError(f"{asset_name} não está na lista de hashes")
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="phonebridge_update_")
    final = os.path.join(dest_dir, os.path.basename(asset_name))
    part = final + ".part"
    h = hashlib.sha256()
    total = 0
    try:
        with _open(assets[asset_name], timeout=30) as r, open(part, "wb") as f:
            while True:
                block = r.read(65536)
                if not block:
                    break
                total += len(block)
                if total > MAX_DOWNLOAD:
                    raise UpdateError("download maior que o permitido")
                h.update(block)
                f.write(block)
                if progress:
                    progress(total)
        if h.hexdigest() != expected:
            raise UpdateError("SHA-256 do arquivo não confere: atualização recusada")
        os.replace(part, final)
        return final
    finally:
        if os.path.exists(part):
            os.remove(part)


def launch_installer(path):
    """Windows: abre o instalador (o usuário confirma). Linux: só informa onde o arquivo ficou."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    return False


def frozen():
    return bool(getattr(sys, "frozen", False))
