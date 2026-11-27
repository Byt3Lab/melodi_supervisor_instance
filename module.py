from __future__ import annotations

import asyncio
from core.module import ApplicationModule, Permission
from modules.melodi_supervisor_instance.controllers.api import SupervisorInstanceAPIController
from modules.melodi_supervisor_instance.controllers.web import SupervisorInstanceWebController
from modules.melodi_supervisor_instance.services.agent_service import SupervisorAgentService


class MelodiSupervisorInstanceModule(ApplicationModule):
    def __init__(self):
        super().__init__(name="melodi_supervisor_instance", router_name="melodi_supervisor_instance")
        self._heartbeat_task: asyncio.Task | None = None

    def _get_permissions(self):
        return [
            Permission(self, "agent:read", description="Consulter le statut de connexion au Superviseur Central"),
            Permission(self, "agent:manage", description="Configurer le pairage, forcer la synchronisation ou déconnecter l'instance"),
        ]

    async def _heartbeat_loop(self):
        backoff = 15
        while True:
            try:
                svc = SupervisorAgentService(self)
                res = await svc.send_heartbeat()
                backoff = 15 if res is not None else min(120, backoff * 2)
            except asyncio.CancelledError:
                break
            except Exception:
                backoff = min(120, backoff * 2)
            await asyncio.sleep(backoff)

    async def _start_heartbeat(self):
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _stop_heartbeat(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

    async def load(self):
        # 1. Appliquer automatiquement le mode 'managed' si déjà pairé en DB
        svc = SupervisorAgentService(self)
        await svc.apply_supervision_mode_from_db()

        # 2. Boucle heartbeat démarrée au serving et arrêtée proprement à after_serving
        if self.app and getattr(self.app, "server", None):
            self.app.server.before_serving(self._start_heartbeat)
            self.app.server.after_serving(self._stop_heartbeat)

        # 3. Routes API
        for router in (self.api_router, self.api_admin_router):
            router.add_route("/status", SupervisorInstanceAPIController.status_api, methods=["GET"])
            router.add_route("/pair", SupervisorInstanceAPIController.pair_api, methods=["POST"])
            router.add_route("/sync", SupervisorInstanceAPIController.sync_now_api, methods=["POST"])
            router.add_route("/disconnect", SupervisorInstanceAPIController.disconnect_api, methods=["POST"])

        # 4. Web SPA Shell
        self.admin_router.add_route("/", SupervisorInstanceWebController.index, methods=["GET"])

        # 5. Navigation
        self.register_navigation(
            id="melodi_supervisor_instance_root",
            label="Supervisor Instance",
            icon="name=shield&fill=white",
            url="/admin/melodi_supervisor_instance/",
            priority=49,
            required_permission="melodi_supervisor_instance.agent:read",
        )


module = MelodiSupervisorInstanceModule()
