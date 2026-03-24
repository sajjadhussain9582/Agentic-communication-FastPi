from fastapi import APIRouter
from app.api.routes.health import router as health_router
from app.api.routes.users import router as users_router
from app.api.routes.auth_supabase import router as auth_supabase_router
from app.api.routes.forms_agent import router as forms_agent_router
from app.api.routes.knowledge_base import router as knowledge_base_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.contacts import router as contacts_router
from app.api.routes.campaigns import router as campaigns_router
from app.api.routes.templates import router as templates_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.webhooks_ghl import router as webhooks_ghl_router
from app.api.routes.ops import router as ops_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(users_router, prefix="/users")
api_router.include_router(auth_supabase_router)
api_router.include_router(forms_agent_router)
api_router.include_router(knowledge_base_router)
api_router.include_router(conversations_router)
api_router.include_router(contacts_router)
api_router.include_router(campaigns_router)
api_router.include_router(templates_router)
api_router.include_router(integrations_router)
api_router.include_router(webhooks_ghl_router)
api_router.include_router(ops_router)