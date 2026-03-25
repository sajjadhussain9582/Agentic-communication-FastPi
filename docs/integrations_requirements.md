# Backend Requirements for Email & Calendly Integration

To make the email and scheduling integrations functional, the following backend tasks are required:

## 1. Email Integration Requirements
- **SMTP Support**: Host, Port, Username, Password, TLS/SSL.
- **API Support**: Resend or SendGrid API Key.
- **Configuration**: Store in `Integration.config_json` for provider `email`.
- **Service**: Implement `app/services/email_service.py` to handle `send_email`.
- **Delivery**: Update `app/services/channel_delivery.py` to use `email_service`.

## 2. Calendly Integration Requirements
- **Connection Types**:
    - **Personal Access Token (PAT)**: For private API access.
    - **OAuth (Optional)**: For a smoother user flow.
    - **Booking URL**: Simple storage of the public booking link.
- **Webhooks**:
    - Implement an endpoint `POST /api/webhooks/calendly`.
    - Handle `invitee.created` and `invitee.canceled` events.
    - Update/Create `Contact` or `Conversation` based on webhook payload.
- **Sync**: (Optional) Initial sync of upcoming events.

## 3. Database & Models
- Use the `Integration` model for both.
- Use `IntegrationRun` to audit outbound emails and inbound webhooks.

## 4. API Endpoints
- `POST /api/integrations/email/configure`: Validates and saves email config.
- `POST /api/integrations/calendly/configure`: Saves PAT or Booking URL.
- `GET /api/integrations`: Returns status for all (including email and scheduling).
