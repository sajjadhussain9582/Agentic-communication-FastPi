# HubSpot Integration Guide for Frontend

This guide outlines how to integrate the HubSpot OAuth flow into the frontend application.

## 1. Connection Flow

### **Step A: Initiate Connection**
When the user clicks the **"Connect HubSpot"** button, redirect them to the backend authorization endpoint.

```javascript
const handleConnectHubSpot = () => {
  const backendUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  window.location.href = `${backendUrl}/api/v1/integrations/hubspot/authorize`;
};
```

### **Step B: Handle Callback Redirect**
After the user approves access in HubSpot, the backend will process the tokens and redirect the user back to your frontend.

**Success Redirect URL:**
`http://localhost:3000/settings/integrations?status=success&provider=hubspot`

**Error Redirect URL:**
`http://localhost:3000/settings/integrations?status=error&provider=hubspot&message=...`

### **Step C: Update UI State**
In your integration settings page, listen for these query parameters to show a success or error notification.

```javascript
useEffect(() => {
  const params = new URLSearchParams(window.location.search);
  const status = params.get('status');
  const provider = params.get('provider');

  if (provider === 'hubspot') {
    if (status === 'success') {
      toast.success("HubSpot connected successfully!");
      // Refresh the integrations list from the backend
    } else if (status === 'error') {
      const msg = params.get('message');
      toast.error(`HubSpot connection failed: ${msg}`);
    }
  }
}, []);
```

## 2. API Endpoints

### **Check Connection Status**
Use the existing integrations list endpoint to see if HubSpot is connected.

**Request:** `GET /api/v1/integrations`

**Response Fragment:**
```json
[
  {
    "provider": "hubspot",
    "status": "connected", // or "disconnected"
    "last_sync_at": "2024-03-30T..."
  }
]
```

### **Disconnect HubSpot**
Use the existing disconnect endpoint to remove the connection.

**Request:** `POST /api/v1/integrations/hubspot/disconnect`

## 3. Scopes Used
The backend is configured to request the following scopes:
- `crm.objects.contacts.read`
- `crm.objects.contacts.write`
- `crm.objects.owners.read`
- `crm.schemas.contacts.read`

Ensure your HubSpot app has these scopes enabled in the **HubSpot Developer Portal**.
