"""Eigene kleine Zertifizierungsstelle (CA) fuers Heimnetz, damit die Seite per HTTPS laeuft.

Chrome installiert eine Web-App nur von HTTPS-Seiten mit vertrauenswuerdigem Zertifikat. Die CA
wird einmal auf dem Handy installiert; das Server-Zertifikat stellt sie fuer die Namen/IPs des PCs
aus. Die CA haelt 10 Jahre, das Server-Zertifikat 397 Tage und wird vorher automatisch erneuert.
"""

import datetime as dt
import ipaddress
import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 3650
SERVER_DAYS = 397  # Chrome lehnt laengere Laufzeiten bei Server-Zertifikaten teils ab
RENEW_BEFORE_DAYS = 30


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _write_key(path, key):
    path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))


def _load_or_create_ca(cert_dir):
    key_path, cert_path = cert_dir / "ca.key", cert_dir / "ca.crt"
    if key_path.exists() and cert_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(cert_path.read_bytes())

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Monitor Ambilight Heimnetz-CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(minutes=5))
        .not_valid_after(_now() + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def ensure_certs(cert_dir, names, ips):
    """Legt CA und Server-Zertifikat an bzw. erneuert sie; liefert (ca.crt, server.crt, server.key)."""
    cert_dir.mkdir(exist_ok=True)
    ca_key, ca_cert = _load_or_create_ca(cert_dir)

    cert_path, key_path, meta_path = cert_dir / "server.crt", cert_dir / "server.key", cert_dir / "server.json"
    wanted = {"names": sorted(names), "ips": sorted(ips)}
    if cert_path.exists() and key_path.exists() and meta_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        still_valid = cert.not_valid_after_utc - _now() > dt.timedelta(days=RENEW_BEFORE_DAYS)
        if still_valid and json.loads(meta_path.read_text(encoding="utf-8")) == wanted:
            return cert_dir / "ca.crt", cert_path, key_path

    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(n) for n in wanted["names"]] + [x509.IPAddress(ipaddress.ip_address(i)) for i in wanted["ips"]]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, wanted["names"][0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(minutes=5))
        .not_valid_after(_now() + dt.timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(key_path, key)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    meta_path.write_text(json.dumps(wanted), encoding="utf-8")
    return cert_dir / "ca.crt", cert_path, key_path
