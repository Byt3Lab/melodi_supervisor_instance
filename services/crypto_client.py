from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
import zipfile
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken


class SupervisorCryptoClient:
    """Primitives cryptographiques côté agent melodi_supervisor_instance (autonome, zéro couplage avec center)."""

    def __init__(self, local_master_secret: str):
        raw = (local_master_secret or "melodi-instance-secret").encode("utf-8")
        key = hashlib.pbkdf2_hmac("sha256", raw, b"melodi_supervisor_instance_salt_v1", 100_000, dklen=32)
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str | None:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, Exception):
            return None

    @staticmethod
    def validate_center_url(url: str, *, env: str = "prod", allow_insecure_http: bool = False) -> tuple[bool, str]:
        cleaned = (url or "").strip().rstrip("/")
        if not cleaned:
            return False, "L'URL du Superviseur Central est requise."
        parsed = urlparse(cleaned)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False, "URL du Superviseur Central invalide (format attendu : https://center.exemple.com)."
        if env == "prod" and parsed.scheme != "https" and not allow_insecure_http:
            return False, "En production, HTTPS est obligatoire pour communiquer avec le Superviseur Central."
        return True, cleaned

    @staticmethod
    def build_signed_headers(
        *,
        instance_id: str,
        api_key: str,
        shared_secret: str,
        method: str,
        path: str,
        body_bytes: bytes = b"",
    ) -> dict[str, str]:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(body_bytes or b"").hexdigest()
        canonical = f"{method.upper()}\n{path}\n{ts}\n{nonce}\n{body_hash}"
        sig = hmac.new(shared_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "X-Supervisor-Instance-Id": instance_id,
            "X-Supervisor-Api-Key": api_key,
            "X-Supervisor-Timestamp": ts,
            "X-Supervisor-Nonce": nonce,
            "X-Supervisor-Signature": sig,
        }

    @staticmethod
    def verify_manifest_signature(shared_secret: str, manifest_payload: dict[str, Any], signature: str) -> bool:
        if not signature or not shared_secret:
            return False
        canonical = json.dumps(manifest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = hmac.new(shared_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip().lower())

    @staticmethod
    def verify_downloaded_zip(zip_path: str, expected_sha256: str | None = None) -> tuple[bool, str]:
        if not os.path.isfile(zip_path):
            return False, "Archive téléchargée introuvable."
        h = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual_hash = h.hexdigest()
        if expected_sha256 and not hmac.compare_digest(actual_hash.lower(), expected_sha256.strip().lower()):
            return False, "Intégrité SHA-256 invalide pour l'archive téléchargée."
        if not zipfile.is_zipfile(zip_path):
            return False, "Le fichier téléchargé n'est pas une archive ZIP valide."
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                raw_name = member.filename
                norm = os.path.normpath(raw_name)
                if (
                    raw_name.startswith("/")
                    or raw_name.startswith("\\")
                    or norm.startswith("..")
                    or "/../" in f"/{raw_name}/"
                    or "\\..\\" in f"\\{raw_name}\\"
                ):
                    return False, f"Archive rejetée (Zip-Slip détecté sur '{raw_name}')."
        return True, "ok"
