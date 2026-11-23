"""Testes do atualizador (assinatura, hash, limites) e das proteções do sysutil."""
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sysutil  # noqa: E402
import updater  # noqa: E402


def keypair():
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return sk, pub


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_release(sk, files, tamper_sums=False, bad_sig=False):
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items()).encode()
    sig = sk.sign(sums if not bad_sig else b"outra coisa")
    store = {"https://github.com/x/SHA256SUMS": sums, "https://github.com/x/SHA256SUMS.sig": sig}
    assets = {"SHA256SUMS": "https://github.com/x/SHA256SUMS", "SHA256SUMS.sig": "https://github.com/x/SHA256SUMS.sig"}
    for n, b in files.items():
        store[f"https://github.com/x/{n}"] = b
        assets[n] = f"https://github.com/x/{n}"
    return {"tag": "v9.9.9", "assets": assets}, store


@pytest.fixture
def net(monkeypatch):
    holder = {"store": {}}
    monkeypatch.setattr(updater, "_open", lambda url, timeout=20: FakeResp(holder["store"][url]))
    return holder


def test_version_parsing_and_compare():
    assert updater.parse_version("v3.0.0") == (3, 0, 0, 1, "")
    assert updater.is_newer("v3.0.1", "3.0.0") and updater.is_newer("4.0.0", "3.9.9")
    assert not updater.is_newer("3.0.0", "3.0.0") and not updater.is_newer("2.9.9", "3.0.0")
    assert not updater.is_newer("3.0.0-rc1", "3.0.0")  # pré-lançamento vale menos que a final
    assert not updater.is_newer("lixo", "3.0.0")
    with pytest.raises(ValueError):
        updater.parse_version("1.2")


def test_check_ignores_drafts_prereleases_and_old(monkeypatch):
    def run(rel, current="3.0.0"):
        return updater.check(current, fetch=lambda u: json.dumps(rel).encode())

    base = {"tag_name": "v3.1.0", "body": "novidades", "html_url": "https://github.com/x", "assets": [
        {"name": "PhoneBridgeSetup.exe", "browser_download_url": "https://github.com/x/a.exe"}]}
    r = run(base)
    assert r["tag"] == "v3.1.0" and r["assets"]["PhoneBridgeSetup.exe"].endswith("a.exe")
    assert run({**base, "prerelease": True}) is None and run({**base, "draft": True}) is None
    assert run(base, current="3.1.0") is None
    with pytest.raises(updater.UpdateError):
        updater.check("3.0.0", fetch=lambda u: b"nao e json")


def test_download_verified_ok(net, tmp_path):
    sk, pub = keypair()
    rel, net["store"] = fake_release(sk, {"PhoneBridgeSetup.exe": b"conteudo do instalador"})
    path = updater.download_verified(rel, "PhoneBridgeSetup.exe", str(tmp_path), public_key_hex=pub)
    assert open(path, "rb").read() == b"conteudo do instalador"
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".part")]


def test_download_rejects_wrong_hash(net, tmp_path):
    sk, pub = keypair()
    rel, store = fake_release(sk, {"PhoneBridgeSetup.exe": b"original"})
    store["https://github.com/x/PhoneBridgeSetup.exe"] = b"adulterado"
    net["store"] = store
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download_verified(rel, "PhoneBridgeSetup.exe", str(tmp_path), public_key_hex=pub)
    assert os.listdir(tmp_path) == []  # nada fica no disco


def test_download_rejects_bad_signature_and_wrong_key(net, tmp_path):
    sk, pub = keypair()
    rel, net["store"] = fake_release(sk, {"a.exe": b"x"}, bad_sig=True)
    with pytest.raises(updater.UpdateError, match="assinatura"):
        updater.download_verified(rel, "a.exe", str(tmp_path), public_key_hex=pub)
    rel, net["store"] = fake_release(sk, {"a.exe": b"x"})
    _, other_pub = keypair()
    with pytest.raises(updater.UpdateError, match="assinatura"):
        updater.download_verified(rel, "a.exe", str(tmp_path), public_key_hex=other_pub)


def test_download_refuses_without_configured_key(net, tmp_path, monkeypatch):
    sk, _ = keypair()
    rel, net["store"] = fake_release(sk, {"a.exe": b"x"})
    monkeypatch.setattr(updater, "UPDATE_PUBLIC_KEY", "")
    with pytest.raises(updater.UpdateError, match="chave de assinatura"):
        updater.download_verified(rel, "a.exe", str(tmp_path))


def test_download_requires_sums_files(net, tmp_path):
    with pytest.raises(updater.UpdateError):
        updater.download_verified({"tag": "v9", "assets": {"a.exe": "https://github.com/x/a.exe"}}, "a.exe", str(tmp_path))


def test_download_size_limit(net, tmp_path, monkeypatch):
    sk, pub = keypair()
    rel, net["store"] = fake_release(sk, {"a.exe": b"x" * 5000})
    monkeypatch.setattr(updater, "MAX_DOWNLOAD", 1000)
    with pytest.raises(updater.UpdateError, match="maior"):
        updater.download_verified(rel, "a.exe", str(tmp_path), public_key_hex=pub)
    assert os.listdir(tmp_path) == []


def test_url_allowlist():
    for bad in ("http://github.com/x", "https://evil.example.com/a", "file:///etc/passwd", "ftp://github.com/a"):
        with pytest.raises(updater.UpdateError):
            updater._check_url(bad)
    updater._check_url("https://github.com/MissionBreak56/PhoneBridge/releases/download/v1/a.exe")


def test_parse_sums():
    h = "ab" * 32
    assert updater.parse_sums(f"{h}  a.exe\n{h.upper()} *b.tar.gz\nlixo\n".encode()) == {"a.exe": h, "b.tar.gz": h}


# ---------------------------------------------------------------- sysutil
def make_zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_zip_slip_is_blocked():
    dest = tempfile.mkdtemp()
    for evil in ("../evil.txt", "a/../../evil.txt", "/etc/evil.txt"):
        with pytest.raises(ValueError):
            sysutil.safe_extract(make_zip(["ok.txt", evil]), dest)
    assert not os.path.exists(os.path.join(os.path.dirname(dest), "evil.txt"))
    sysutil.safe_extract(make_zip(["VBCABLE_Setup_x64.exe", "sub/readme.txt"]), dest)
    assert os.path.exists(os.path.join(dest, "sub", "readme.txt"))


def test_zip_symlink_is_blocked():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    buf.seek(0)
    with pytest.raises(ValueError):
        sysutil.safe_extract(zipfile.ZipFile(buf), tempfile.mkdtemp())


def test_firewall_script_only_touches_own_rules_and_is_opt_in():
    script = sysutil._setup_script(5800, firewall=True, private=False)
    assert "PhoneBridge 5800" in script and sysutil.FW_UDP_NAME in script
    assert "Set-NetConnectionProfile" not in script  # mudar redes é um passo separado e opcional
    assert "PhoneBridge*" not in script  # nada de apagar por curinga
    assert "-match '^PhoneBridge( \\d+| descoberta)$'" in script
    assert "Set-NetConnectionProfile" in sysutil._setup_script(5800, firewall=False, private=True)
    assert "New-NetFirewallRule" not in sysutil._setup_script(5800, firewall=False, private=True)


def test_vbcable_refuses_without_pinned_hash(monkeypatch):
    msgs = []
    monkeypatch.setattr(sysutil, "IS_WIN", True)
    monkeypatch.setattr(sysutil, "VBCABLE_SHA256", "")
    assert sysutil.install_vbcable(msgs.append) == "manual" and msgs
