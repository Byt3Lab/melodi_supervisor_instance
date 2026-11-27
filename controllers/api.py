from __future__ import annotations

from core.routing import Request, Response
from core.routing.decorator import require_permission
from modules.melodi_supervisor_instance.services.agent_service import SupervisorAgentService


class SupervisorInstanceAPIController:
    @staticmethod
    @require_permission("melodi_supervisor_instance.agent:read")
    async def status_api(request: Request, response: Response):
        svc = SupervisorAgentService(request.module)
        return response.render_json({"success": True, "data": await svc.get_public_status()})

    @staticmethod
    @require_permission("melodi_supervisor_instance.agent:manage")
    async def pair_api(request: Request, response: Response):
        svc = SupervisorAgentService(request.module)
        body = request.json or {}
        res, code = await svc.pair_with_center(
            center_url=str(body.get("center_url") or ""),
            enrollment_token=str(body.get("enrollment_token") or ""),
            instance_name=body.get("instance_name"),
            allow_insecure_http=bool(body.get("allow_insecure_http", False)),
        )
        return response.render_json(res, status_code=code)

    @staticmethod
    @require_permission("melodi_supervisor_instance.agent:manage")
    async def sync_now_api(request: Request, response: Response):
        svc = SupervisorAgentService(request.module)
        res = await svc.sync_with_center()
        return response.render_json(res, status_code=200 if res.get("success") else 400)

    @staticmethod
    @require_permission("melodi_supervisor_instance.agent:manage")
    async def disconnect_api(request: Request, response: Response):
        import json
        user_raw = request.get_session("user_payload") or request.data_context.get("user_payload") or getattr(request, "user", None)
        user = json.loads(user_raw) if isinstance(user_raw, str) else user_raw
        is_sudo = bool(user.get("is_sudo")) if isinstance(user, dict) else bool(getattr(user, "is_sudo", False))
        if not is_sudo:
            return response.render_json(
                {"success": False, "error": "Seul un super-administrateur (sudo) peut déconnecter l'instance du Superviseur Central."},
                status_code=403,
            )
        svc = SupervisorAgentService(request.module)
        res = await svc.disconnect_from_center()
        return response.render_json(res, status_code=200)
