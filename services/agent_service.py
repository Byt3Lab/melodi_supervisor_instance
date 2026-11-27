from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
from datetime import datetime
from typing import Any

import httpx

from core.service import Service
from modules.melodi_supervisor_instance.services.crypto_client import SupervisorCryptoClient

CFG_PREFIX = "melodi_supervisor_instance"
CALLER_NAME = "melodi_supervisor_instance"


class SupervisorAgentService(Service):
    _sync_locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self) -> asyncio.Lock:
        app_id = id(self.app)
        if app_id not in self._sync_locks:
            self._sync_locks[app_id] = asyncio.Lock()
        return self._sync_locks[app_id]

    def _crypto(self) -> SupervisorCryptoClient:
        secret = getattr(getattr(self.app, "config", None), "secret_key", "") or "melodi-instance-secret"
        return SupervisorCryptoClient(str(secret))

    async def _call_base_hook(self, action_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        req = dict(payload or {})
        req["_caller_module"] = CALLER_NAME
        return await self.app.hook_manager.action.execute("base", action_name, req)

    async def load_pairing_config(self) -> dict[str, Any]:
        paired = await self.app.get_config_db("paired", prefix=CFG_PREFIX, default="false")
        is_paired = str(paired).strip().lower() == "true"
        center_url = await self.app.get_config_db("center_url", prefix=CFG_PREFIX, default="")
        instance_id = await self.app.get_config_db("instance_id", prefix=CFG_PREFIX, default="")
        api_key_enc = await self.app.get_config_db("api_key_enc", prefix=CFG_PREFIX, default="")
        secret_enc = await self.app.get_config_db("shared_secret_enc", prefix=CFG_PREFIX, default="")
        cluster_id = await self.app.get_config_db("cluster_id", prefix=CFG_PREFIX, default="")
        last_sync_at = await self.app.get_config_db("last_sync_at", prefix=CFG_PREFIX, default="")
        last_sync_status = await self.app.get_config_db("last_sync_status", prefix=CFG_PREFIX, default="")
        synced_version = await self.app.get_config_db("synced_manifest_version", prefix=CFG_PREFIX, default="0")

        crypto = self._crypto()
        api_key = crypto.decrypt(api_key_enc) if api_key_enc else ""
        shared_secret = crypto.decrypt(secret_enc) if secret_enc else ""

        return {
            "paired": bool(is_paired and center_url and instance_id and api_key and shared_secret),
            "center_url": str(center_url or "").rstrip("/"),
            "instance_id": str(instance_id or ""),
            "api_key": api_key or "",
            "shared_secret": shared_secret or "",
            "cluster_id": str(cluster_id or "") or None,
            "last_sync_at": last_sync_at or None,
            "last_sync_status": last_sync_status or None,
            "synced_manifest_version": int(synced_version or 0),
        }

    async def apply_supervision_mode_from_db(self) -> bool:
        cfg = await self.load_pairing_config()
        if cfg["paired"]:
            self.app.supervision_mode = "managed"
            self.app.supervision_owner = CALLER_NAME
            if hasattr(self.app, "default_global_template_context"):
                self.app.default_global_template_context["supervision_mode"] = "managed"
            return True
        self.app.supervision_mode = "standalone"
        self.app.supervision_owner = None
        if hasattr(self.app, "default_global_template_context"):
            self.app.default_global_template_context["supervision_mode"] = "standalone"
        return False

    async def get_public_status(self) -> dict[str, Any]:
        cfg = await self.load_pairing_config()
        inventory = await self._call_base_hook("supervisor_get_inventory")
        return {
            "paired": cfg["paired"],
            "supervision_mode": getattr(self.app, "supervision_mode", "standalone"),
            "center_url": cfg["center_url"],
            "instance_id": cfg["instance_id"],
            "cluster_id": cfg["cluster_id"],
            "last_sync_at": cfg["last_sync_at"],
            "last_sync_status": cfg["last_sync_status"],
            "synced_manifest_version": cfg["synced_manifest_version"],
            "inventory": inventory,
        }

    async def pair_with_center(
        self,
        *,
        center_url: str,
        enrollment_token: str,
        instance_name: str | None = None,
        allow_insecure_http: bool = False,
    ) -> tuple[dict[str, Any], int]:
        server_mode = str(getattr(self.app.config, "SERVER_MODE", "central")).lower()
        if server_mode != "central":
            return {"success": False, "error": "L'agent Superviseur ne peut être pairé que sur une instance de type 'central'."}, 400

        env = str((getattr(self.app, "running_config", None) or {}).get("env") or os.environ.get("MELODI_ENV", "dev")).lower()
        crypto = self._crypto()
        ok_url, cleaned_or_err = crypto.validate_center_url(center_url, env=env, allow_insecure_http=allow_insecure_http)
        if not ok_url:
            return {"success": False, "error": cleaned_or_err}, 400

        cleaned_url = cleaned_or_err
        inventory = await self._call_base_hook("supervisor_get_inventory")
        payload = {
            "enrollment_token": enrollment_token.strip(),
            "name": (instance_name or socket.gethostname() or "melodi-instance").strip(),
            "hostname": socket.gethostname(),
            "server_mode": server_mode,
            "boot_id": inventory.get("boot_id"),
            "boot_ts": inventory.get("boot_ts"),
            "core_version": inventory.get("core_version"),
            "is_docker": inventory.get("is_docker", False),
            "inventory": inventory,
        }

        pair_endpoint = f"{cleaned_url}/api/melodi_supervisor_center/agent/pair"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(pair_endpoint, json=payload)
                body = resp.json() if resp.content else {}
        except Exception as e:
            return {"success": False, "error": f"Impossible de joindre le Superviseur Central : {str(e)}"}, 502

        if resp.status_code not in (200, 201) or not body.get("success"):
            return {"success": False, "error": body.get("error") or f"Échec du pairage (HTTP {resp.status_code})."}, resp.status_code

        data = body.get("data") or {}
        await self.app.save_config_db("paired", "true", prefix=CFG_PREFIX)
        await self.app.save_config_db("center_url", cleaned_url, prefix=CFG_PREFIX)
        await self.app.save_config_db("instance_id", data["instance_id"], prefix=CFG_PREFIX)
        await self.app.save_config_db("api_key_enc", crypto.encrypt(data["api_key"]), prefix=CFG_PREFIX)
        await self.app.save_config_db("shared_secret_enc", crypto.encrypt(data["shared_secret"]), prefix=CFG_PREFIX)
        await self.app.save_config_db("cluster_id", data.get("cluster_id") or "", prefix=CFG_PREFIX)
        await self.app.save_config_db("synced_manifest_version", "0", prefix=CFG_PREFIX)

        await self.apply_supervision_mode_from_db()

        # Convergence initiale immédiate avec le schéma du center
        sync_result = await self.sync_with_center()
        return {
            "success": True,
            "message": "Instance enrôlée et passée en mode supervisé ('managed').",
            "data": {"instance_id": data["instance_id"], "sync": sync_result},
        }, 200

    async def disconnect_from_center(self) -> dict[str, Any]:
        """Procédure d'urgence (Break-Glass) : déconnecte l'instance et repasse en mode 'standalone'."""
        for key in ("paired", "center_url", "instance_id", "api_key_enc", "shared_secret_enc", "cluster_id"):
            await self.app.save_config_db(key, "", prefix=CFG_PREFIX)
        await self.apply_supervision_mode_from_db()
        return {"success": True, "supervision_mode": "standalone", "message": "Instance déconnectée du Superviseur Central."}

    async def send_heartbeat(self) -> dict[str, Any] | None:
        cfg = await self.load_pairing_config()
        if not cfg["paired"]:
            return None

        inventory = await self._call_base_hook("supervisor_get_inventory")
        body_dict = {
            "hostname": socket.gethostname(),
            "boot_id": inventory.get("boot_id"),
            "boot_ts": inventory.get("boot_ts"),
            "core_version": inventory.get("core_version"),
            "is_docker": inventory.get("is_docker", False),
            "inventory": inventory,
        }
        body_bytes = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        path = "/api/melodi_supervisor_center/agent/heartbeat"
        headers = self._crypto().build_signed_headers(
            instance_id=cfg["instance_id"],
            api_key=cfg["api_key"],
            shared_secret=cfg["shared_secret"],
            method="POST",
            path=path,
            body_bytes=body_bytes,
        )
        headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{cfg['center_url']}{path}", content=body_bytes, headers=headers)
            if resp.status_code != 200:
                return None
            res = resp.json()

        data = (res or {}).get("data") or {}
        cmd = data.get("command")
        if cmd == "restart":
            await self._call_base_hook("supervisor_restart", {"delay": 0.3})
        elif cmd == "soft_reload":
            await self._call_base_hook("supervisor_soft_reload")
        elif cmd == "sync" or data.get("needs_sync"):
            await self.sync_with_center()

        return data

    async def sync_with_center(self) -> dict[str, Any]:
        """Récupère le manifeste signé, calcule le diff et applique les mises à jour via les hooks de base."""
        cfg = await self.load_pairing_config()
        if not cfg["paired"]:
            return {"success": False, "error": "Instance non pairée."}

        async with self._get_lock():
            crypto = self._crypto()
            manifest_path = "/api/melodi_supervisor_center/agent/manifest"
            headers = crypto.build_signed_headers(
                instance_id=cfg["instance_id"],
                api_key=cfg["api_key"],
                shared_secret=cfg["shared_secret"],
                method="GET",
                path=manifest_path,
            )

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    m_resp = await client.get(f"{cfg['center_url']}{manifest_path}", headers=headers)
                    m_body = m_resp.json() if m_resp.content else {}
            except Exception as e:
                return {"success": False, "error": f"Erreur réseau manifeste : {str(e)}"}

            if m_resp.status_code != 200 or not m_body.get("success"):
                return {"success": False, "error": m_body.get("error") or "Impossible de lire le manifeste."}

            manifest = m_body.get("manifest") or {}
            signature = m_body.get("signature") or ""
            if not crypto.verify_manifest_signature(cfg["shared_secret"], manifest, signature):
                err = "Signature HMAC du manifeste invalide (rejet sécurité)."
                await self._send_report(cfg, status="error", manifest_version=manifest.get("manifest_version"), error=err)
                return {"success": False, "error": err}

            inventory = await self._call_base_hook("supervisor_get_inventory")
            local_mods = {m["name"]: m for m in (inventory.get("modules") or [])}
            actions_done: list[str] = []
            needs_restart = False
            needs_soft_reload = False

            try:
                # 1. Core update
                core_spec = manifest.get("core")
                if core_spec and core_spec.get("artifact_id") and core_spec.get("version") != inventory.get("core_version"):
                    if not inventory.get("core_update_blocked"):
                        zip_path = await self._download_and_verify_artifact(cfg, core_spec["artifact_id"], core_spec.get("sha256"))
                        try:
                            res = await self._call_base_hook(
                                "supervisor_update_core",
                                {"zip_path": zip_path, "expected_sha256": core_spec.get("sha256"), "filename": core_spec.get("filename")},
                            )
                            if not res.get("success"):
                                raise RuntimeError(res.get("error") or res.get("message") or "Échec mise à jour Core")
                            actions_done.append(f"core:{core_spec.get('version')}")
                            needs_restart = True
                        finally:
                            if os.path.exists(zip_path):
                                os.remove(zip_path)

                # 2. Modules diff (install/update + toggle)
                for mod_spec in (manifest.get("modules") or []):
                    m_name = mod_spec.get("name")
                    if not m_name or m_name in ("base", CALLER_NAME):
                        continue
                    local = local_mods.get(m_name)
                    target_ver = mod_spec.get("version")
                    desired_active = bool(mod_spec.get("active", True))
                    desired_absent = bool(mod_spec.get("remove", False))

                    if desired_absent:
                        if local:
                            r = await self._call_base_hook("supervisor_remove_module", {"module_name": m_name})
                            if not r.get("success"):
                                raise RuntimeError(r.get("error") or r.get("message") or f"Échec suppression {m_name}")
                            actions_done.append(f"remove:{m_name}")
                            needs_restart = True
                        continue

                    if mod_spec.get("artifact_id") and (not local or (target_ver and local.get("version") != target_ver)):
                        zip_path = await self._download_and_verify_artifact(cfg, mod_spec["artifact_id"], mod_spec.get("sha256"))
                        try:
                            r = await self._call_base_hook(
                                "supervisor_install_module",
                                {
                                    "zip_path": zip_path,
                                    "module_name": m_name,
                                    "expected_sha256": mod_spec.get("sha256"),
                                    "filename": mod_spec.get("filename"),
                                    "allow_update": True,
                                    "allow_upgrade": True,
                                },
                            )
                            if not r.get("success"):
                                raise RuntimeError(r.get("error") or r.get("message") or f"Échec install {m_name}")
                            actions_done.append(f"install:{m_name}@{target_ver}")
                            needs_restart = True
                        finally:
                            if os.path.exists(zip_path):
                                os.remove(zip_path)

                    current_active = bool(local.get("is_active")) if local else False
                    if desired_active != current_active:
                        r = await self._call_base_hook("supervisor_toggle_module", {"module_name": m_name, "active": desired_active})
                        if not r.get("success"):
                            raise RuntimeError(r.get("error") or f"Échec toggle {m_name}")
                        actions_done.append(f"toggle:{m_name}={'on' if desired_active else 'off'}")
                        needs_restart = True

                # 3. Languages diff
                for lang_spec in (manifest.get("languages") or []):
                    if lang_spec.get("artifact_id"):
                        zip_path = await self._download_and_verify_artifact(cfg, lang_spec["artifact_id"], lang_spec.get("sha256"))
                        try:
                            r = await self._call_base_hook(
                                "supervisor_install_language",
                                {
                                    "zip_path": zip_path,
                                    "target_module": lang_spec.get("module"),
                                    "filename": lang_spec.get("filename"),
                                },
                            )
                            if not r.get("success"):
                                raise RuntimeError(r.get("error") or r.get("message") or "Échec installation langue")
                            actions_done.append(f"lang:{lang_spec.get('module')}:{lang_spec.get('code')}")
                            needs_soft_reload = True
                        finally:
                            if os.path.exists(zip_path):
                                os.remove(zip_path)

                mv = int(manifest.get("manifest_version") or 1)
                await self.app.save_config_db("synced_manifest_version", str(mv), prefix=CFG_PREFIX)
                await self.app.save_config_db("last_sync_at", datetime.utcnow().isoformat() + "Z", prefix=CFG_PREFIX)
                await self.app.save_config_db("last_sync_status", "ok", prefix=CFG_PREFIX)
                await self._send_report(cfg, status="ok", manifest_version=mv, actions=actions_done)

                if needs_restart:
                    await self._call_base_hook("supervisor_restart", {"delay": 0.4})
                elif needs_soft_reload:
                    await self._call_base_hook("supervisor_soft_reload")

                return {"success": True, "manifest_version": mv, "actions": actions_done, "restarting": needs_restart}
            except Exception as exc:
                await self.app.save_config_db("last_sync_at", datetime.utcnow().isoformat() + "Z", prefix=CFG_PREFIX)
                await self.app.save_config_db("last_sync_status", "error", prefix=CFG_PREFIX)
                await self._send_report(cfg, status="error", manifest_version=manifest.get("manifest_version"), error=str(exc), actions=actions_done)
                return {"success": False, "error": str(exc), "actions": actions_done}

    async def _download_and_verify_artifact(self, cfg: dict[str, Any], artifact_id: str, expected_sha256: str | None) -> str:
        crypto = self._crypto()
        dl_path = f"/api/melodi_supervisor_center/agent/artifacts/{artifact_id}/download"
        headers = crypto.build_signed_headers(
            instance_id=cfg["instance_id"],
            api_key=cfg["api_key"],
            shared_secret=cfg["shared_secret"],
            method="GET",
            path=dl_path,
        )
        fd, tmp_path = tempfile.mkstemp(prefix="melodi_sup_", suffix=".zip")
        os.close(fd)
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", f"{cfg['center_url']}{dl_path}", headers=headers) as resp:
                if resp.status_code != 200:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise RuntimeError(f"Téléchargement artefact {artifact_id} échoué (HTTP {resp.status_code}).")
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)

        ok, err = crypto.verify_downloaded_zip(tmp_path, expected_sha256=expected_sha256)
        if not ok:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(err)
        return tmp_path

    async def _send_report(
        self,
        cfg: dict[str, Any],
        *,
        status: str,
        manifest_version: int | None = None,
        error: str | None = None,
        actions: list[str] | None = None,
    ) -> None:
        try:
            body_bytes = json.dumps(
                {
                    "status": status,
                    "manifest_version": manifest_version,
                    "error": error,
                    "actions": actions or [],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            path = "/api/melodi_supervisor_center/agent/report"
            headers = self._crypto().build_signed_headers(
                instance_id=cfg["instance_id"],
                api_key=cfg["api_key"],
                shared_secret=cfg["shared_secret"],
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )
            headers["Content-Type"] = "application/json"
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(f"{cfg['center_url']}{path}", content=body_bytes, headers=headers)
        except Exception:
            pass
