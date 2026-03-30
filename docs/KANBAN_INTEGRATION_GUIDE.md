# Lead Flow Kanban Integration Guide

This guide provides the frontend team with the necessary API details and data structures to integrate the dynamic Lead Flow board (Kanban). The board is powered by a dynamic set of pipeline stages and contact data from the backend.

---

## 1. Dynamic Pipeline Stages

The columns on the Kanban board should not be hardcoded. Fetch the stages first to build the board structure.

### **GET `/api/v1/pipeline-stages`**
**Description**: Returns the list of all active pipeline stages ordered by their `order_index`.

**Response Shape**:
```json
[
  {
    "id": 1,
    "key": "discovery",
    "pipelinestage": "Discovery",
    "order_index": 1,
    "ai_instructions": "..."
  },
  {
    "id": 2,
    "key": "qualified",
    "pipelinestage": "Qualified",
    "order_index": 2,
    "ai_instructions": "..."
  }
  // ... rest of the stages
]
```
**Frontend Action**: Use the `key` as the unique identifier for the column and `pipelinestage` as the display label.

---

## 2. Kanban Board Data

Fetch the leads grouped by their current pipeline stage.

### **GET `/api/v1/contacts/kanban`**
**Description**: Returns all contacts grouped into their respective pipeline stages.

**Response Shape**:
```json
{
  "discovery": [
    {
      "uuid": "550e8400-e29b-41d4-a716-446655440000",
      "email": "lead@example.com",
      "username": "John Doe",
      "company": "Tech Corp",
      "stage": "discovery",
      "status": "active",
      "lead_score": 85.5,
      "updated_at": "2024-03-30T10:00:00Z"
    }
  ],
  "qualified": [],
  "proposal_ready": [],
  "negotiation": [],
  "won": [],
  "lost": []
}
```

---

## 3. Updating Lead Stage (Drag & Drop)

When a user drags a lead from one column to another, update the backend using the PATCH endpoint.

### **PATCH `/api/v1/contacts/{contact_uuid}`**
**Description**: Updates the contact's stage. This automatically updates the `pipeline_stage_id` on the backend.

**Payload**:
```json
{
  "stage": "qualified"
}
```

**Response**: Returns the updated `ContactDetailRead` object.

---

## 4. Frontend UI Components mapping

Based on the provided screenshot, map the following fields to the Kanban Card UI:

| UI Element | Backend Field |
| :--- | :--- |
| **Title / Name** | `username` (fallback to `email`) |
| **Subtitle** | `company` |
| **Lead Value / Score** | `lead_score` (Format as currency or score) |
| **Last Activity** | `updated_at` (Format as "2h ago") |
| **Stage Key** | `stage` |

---

## 5. Implementation Workflow for Frontend

1.  **Fetch Stages**: Call `GET /api/v1/pipeline-stages` to initialize columns.
2.  **Fetch Leads**: Call `GET /api/v1/contacts/kanban` to populate the columns.
3.  **Handle Move**: On `onDragEnd`, call `PATCH /api/v1/contacts/{uuid}` with the new `stage` key.
4.  **Real-time Update**: (Recommended) Refresh the board data after a successful patch or use local state update for snappy UI.

---

**Note**: All API calls require the `Authorization: Bearer <token>` header.
