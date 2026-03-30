# Import all table models so SQLModel.metadata.create_all registers them.
from app.models.user import User  # noqa: F401
from app.models.contact import Contact  # noqa: F401
from app.models.form import Form  # noqa: F401
from app.models.form_submission import FormSubmission  # noqa: F401
from app.models.conversation import Conversation  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.knowledge_base import KnowledgeBaseEntry  # noqa: F401
from app.models.campaign import Campaign, CampaignTarget, CampaignMessageRow  # noqa: F401
from app.models.template import MessageTemplate  # noqa: F401
from app.models.integration import Integration, IntegrationRun, WebhookEvent  # noqa: F401
from app.models.outcome import Outcome  # noqa: F401
from app.models.pipeline_stage import PipelineStage  # noqa: F401
