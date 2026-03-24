# Backend Requirements for Email Integration

To make the email integration functional, the following backend tasks are required:

## 1. Requirement: API Key / Credentials
The system should support at least one of the following:
- **SMTP**: Host, Port, Username, Password, Use TLS/SSL.
- **API Provier (e.g., Resend, SendGrid)**: API Key.

## 2. Database & Models
- Use the existing `Integration` model.
- Store configuration details in `config_json`.
- Suggested structure for `config_json`:
  ```json
  {
    "type": "smtp", 
    "smtp_host": "...",
    "smtp_port": 587,
    "smtp_user": "...",
    "smtp_pass": "...",
    "from_email": "..."
  }
  // OR
  {
    "type": "resend",
    "api_key": "re_...",
    "from_email": "..."
  }
  ```

## 3. New API Endpoint
Implement `POST /api/integrations/email/configure`:
- Accepts the configuration JSON.
- Validates the credentials by sending a "test" email.
- If successful, updates the `Integration` row for `provider='email'` status to `connected`.

## 4. Email Service Implementation
Create `app/services/email_service.py`:
- Implement a `send_email` function that reads the configuration from the database.
- Use `smtplib` for SMTP or `httpx` for API providers.

## 5. Delivery Logic
Update `app/services/channel_delivery.py`:
- In `deliver_message`, if `channel == "email"`:
  - Retrieve the email integration configuration.
  - Call `email_service.send_email`.
  - Log the result in `IntegrationRun`.
