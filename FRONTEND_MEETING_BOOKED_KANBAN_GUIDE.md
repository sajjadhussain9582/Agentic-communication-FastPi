# Frontend Guide: Adding "Meeting Booked" Stage to Kanban

## Overview
The backend now includes a new "Meeting Booked" pipeline stage that gets automatically set when a lead books a meeting via Calendly polling. This guide explains how to integrate this stage into the frontend Kanban board.

## Backend Changes
- Added "Meeting Booked" stage to `pipeline_stages` table with key `meeting_booked`
- API endpoint `/api/v1/pipeline-stages` returns all stages ordered by `order_index`
- Contacts get moved to this stage when Calendly detects a new booking

## Frontend Implementation

### 1. Fetch Pipeline Stages Dynamically
If not already implemented, update your Kanban component to fetch stages from the API:

```javascript
// In your Kanban component
const [pipelineStages, setPipelineStages] = useState([]);

useEffect(() => {
  fetch('/api/v1/pipeline-stages')
    .then(res => res.json())
    .then(data => {
      setPipelineStages(data);
    })
    .catch(err => console.error('Failed to fetch pipeline stages:', err));
}, []);
```

### 2. Render Stages in Kanban
Map through the fetched stages to create columns:

```javascript
{pipelineStages.map(stage => (
  <KanbanColumn
    key={stage.key}
    title={stage.pipelinestage}
    stageKey={stage.key}
    contacts={contacts.filter(c => c.pipeline_stage === stage.key)}
  />
))}
```

### 3. Contact Filtering
Ensure contacts are filtered by `pipeline_stage` field:

```javascript
const contactsInStage = contacts.filter(contact => 
  contact.pipeline_stage === stage.key
);
```

### 4. Stage Order
The stages are ordered by `order_index` from the API, so they will appear in the correct sequence:
1. Discovery
2. Qualified  
3. Meeting Booked (new)
4. Proposal Ready
5. Negotiation
6. Won
7. Lost

### 5. Real-time Updates
When Calendly polling detects a booking, the contact's `pipeline_stage` updates to `meeting_booked`. Make sure your frontend refreshes contact data periodically or via WebSocket to show the move.

### 6. Visual Indicators
Consider adding special styling for the "Meeting Booked" stage:
- Green background or checkmark icon
- Tooltip showing booking details from `contact.external_ids.calendly`

## Testing
1. Book a test meeting on Calendly
2. Wait for worker to poll (every 5 minutes)
3. Check that contact moves to "Meeting Booked" column
4. Verify booking metadata is stored and displayable

## Notes
- The stage appears automatically once you add it to the DB (as per previous instructions)
- No frontend code changes needed if stages are already fetched dynamically
- Ensure API authentication headers are included in fetch requests</content>
<parameter name="filePath">/home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/FRONTEND_MEETING_BOOKED_KANBAN_GUIDE.md