"""Self-signed certificate generation for optional HTTPS (unlocks WebCodecs H.264 in browsers)."""
from __future__ import annotations

import datetime
import os
import platform

from .netutil import local_ips

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".lanremote")


def ensure_certificate() -> tuple[str, str]:
    cert_path = os.path.join(CONFIG_DIR, "cert.pem")
    key_path = os.path.join(CONFIG_DIR, "key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    try:
        import ipaddress

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError as e:
        raise RuntimeError("HTTPS 需要 cryptography 套件：pip install cryptography") from e

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"LAN Remote {platform.node()}")])
    sans = [x509.DNSName("localhost"), x509.DNSName(platform.node() or "lanremote"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in local_ips():
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
