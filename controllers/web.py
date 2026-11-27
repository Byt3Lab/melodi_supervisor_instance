from core.page_builder import Page, Component
from core.routing import Request, Response
from core.routing.decorator import require_permission
from base.components import admin_layout


class SupervisorInstanceWebController:
    @staticmethod
    @require_permission("melodi_supervisor_instance.agent:read")
    async def index(request: Request, response: Response):
        content = Component(module_name="melodi_supervisor_instance", name="supervisor_instance_spa")
        content.add_template("spa.html")
        page = Page(
            request.module.t("supervisor_instance.title", "MELODI Supervisor Instance"),
            admin_layout(content),
        )
        return await response.render_page(page)
