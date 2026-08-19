#!/usr/bin/env python3
"""
setup_tls.py — Generate (or regenerate) the self-signed TLS cert for the DnD
display companion.

Run this once, or any time your LAN IP changes:

    python3 setup_tls.py

Produces cert.pem + key.pem in the writable runtime dir (update-safe), so the
cert survives plugin updates and devices don't have to re-trust it each time.
SANs cover: localhost, 127.0.0.1, and the current LAN IP (en0 / en1 / wlan0).

After regenerating, restart the display:
    bash start-display.sh
"""

import ipaddress
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPLAY_DIR)
from runtime_paths import rt          # writable runtime dir (update-safe)
CERT_FILE   = rt("cert.pem")
KEY_FILE    = rt("key.pem")


def _lan_ip() -> Optional[str]:
    """Return the primary LAN IPv4 address, or None if not found."""
    # macOS: try common interface names
    for iface in ("en0", "en1", "en2"):
        try:
            out = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                stderr=subprocess.DEVNULL,
                encoding="utf-8", text=True,
            ).strip()
            if out:
                return out
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Linux fallback
    try:
        out = subprocess.check_output(
            ["hostname", "-I"],
            stderr=subprocess.DEVNULL,
            encoding="utf-8", text=True,
        ).strip()
        first = out.split()[0] if out.split() else ""
        if first:
            return first
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Last resort: connect to external address and inspect local socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


def _generate_cert(lan_ip: Optional[str]) -> None:
    """Generate self-signed cert using cryptography library (pure Python)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("cryptography package not installed — falling back to openssl CLI")
        _generate_cert_openssl(lan_ip)
        return

    # Key
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    # SANs
    sans: List[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    if lan_ip:
        try:
            sans.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
        except ValueError:
            pass

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dnd-display")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dnd-display")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .sign(key, hashes.SHA256())
    )

    with open(KEY_FILE, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    os.chmod(KEY_FILE, 0o600)


def _generate_cert_openssl(lan_ip: Optional[str]) -> None:
    """Fallback: generate cert via openssl CLI."""
    san = "DNS:localhost,IP:127.0.0.1"
    if lan_ip:
        san += f",IP:{lan_ip}"

    cmd = [
        "openssl", "req", "-x509",
        "-newkey", "rsa:4096",
        "-keyout", KEY_FILE,
        "-out",    CERT_FILE,
        "-days",   "3650",
        "-nodes",
        "-subj",   "/CN=dnd-display",
        "-addext",  f"subjectAltName={san}",
    ]
    subprocess.check_call(cmd, stderr=subprocess.DEVNULL)
    os.chmod(KEY_FILE, 0o600)


def main() -> None:
    lan_ip = _lan_ip()

    print("DnD Display — TLS cert setup")
    print(f"  Output dir : {DISPLAY_DIR}")
    print(f"  LAN IP     : {lan_ip or '(not detected — localhost only)'}")
    print()

    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        ans = input("cert.pem + key.pem already exist. Regenerate? [y/N] ").strip().lower()
        if ans != "y":
            print("Keeping existing certs.")
            _print_urls(lan_ip)
            return

    print("Generating 4096-bit RSA cert (may take a few seconds)…")
    try:
        _generate_cert(lan_ip)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Done.")
    print(f"  cert.pem → {CERT_FILE}")
    print(f"  key.pem  → {KEY_FILE}")
    print()
    _print_urls(lan_ip)
    print()
    print("Restart the display to apply:")
    print(f"  pkill -f dnd-display-app.py ; bash {DISPLAY_DIR}/start-display.sh")


def _print_urls(lan_ip: Optional[str]) -> None:
    print("Access URLs (after restart):")
    print("  Localhost : https://localhost:5001")
    if lan_ip:
        print(f"  LAN       : https://{lan_ip}:5001")
    print()
    print("Phone/tablet: open the LAN URL, accept the browser security warning")
    print("(one-time — tap Advanced → Proceed).")


if __name__ == "__main__":
    main()
