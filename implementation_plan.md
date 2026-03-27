* LangGraph Decision Workflows Refactor Plan

Based on the "Thinking in LangGraph" documentation and the current state of [ai_graph.py](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py), we need to transition from our current **linear pipeline** ([retrieve](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#309-331) → [decide](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#332-376) → [reply](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#377-426)) into a **true state machine** with discrete nodes, structured state, and dynamic routing using `Command`.

## Current State vs. Target State

- **Current State:** [node_decide](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#332-376) outputs a massive JSON string, which is parsed manually. [node_reply](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#377-426) handles drafting the message *and* triggering side-effects (like CRM syncs).
- **Target State:** We will split operations into Data Nodes (Retrieval), LLM Nodes (Classification, Drafting), Action Nodes (Pipeline sync, Booking, SMS), and use LangGraph's native `Command` router to move between them. State will hold raw objects, not stringified JSON.

## Proposed Architecture Shifts

### 1. State Redesign ([AgentState](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#31-48))

We will move away from flat strings and use typed dictionaries. We will introduce `with_structured_output` for classification.

```python
class MessageClassification(TypedDict):
    intent_type: str
    current_pipeline_stage: str
    new_pipeline_stage: str # The stage the AI decides to move them to
    missing_qualification_fields: list[str]
    is_escalated: bool
    requires_action: list[str] # e.g., ["update_status", "send_calendly", "send_email_proposal"]

class AgentState(TypedDict):
    # Raw Inputs
    user_text: str
    channel: str
    # Typed Outputs
    classification: MessageClassification | None
    rag_chunks: list[dict] | None
    generated_reply: str | None
```

### 2. Node Granularity

We will break the graph into single-responsibility nodes:

* **`node_classify` (LLM Step):** Reads the message and returns the `MessageClassification` dict. Crucially, this node looks at the *current* pipeline stage and decides if the lead has provided enough info to move to the *next* pipeline stage (e.g. they provided budget, move from `discovery` to `qualified`).
  * *If needs human:* `goto="node_human_review"`
  * *If stage change needed:* `goto="node_update_status"`
  * *Otherwise:* `goto="node_retrieve"`
* **[node_retrieve](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#309-331) (Data Step):** Uses the existing `match_knowledge_base()` logic to find FAQs.
  * *Always routes to:* `goto="node_draft"`
* **`node_update_status` (Action Step):** Runs Python code to update your local Postgres [Contact](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/models/contact.py#16-52) table with the new stage (e.g. `qualified`, `proposal_ready`).
  * *After updating DB:* `goto="node_retrieve"`
* **`node_draft` (LLM Step):** This is where the magic happens. The drafting prompt changes dynamically based on the pipeline stage:
  * **If `discovery`:** System prompt instructs the AI: *"Ask for missing budget/timeline. DO NOT send the Calendly link."*
  * **If `qualified`:** System prompt instructs the AI: *"The lead is qualified. Propose a meeting and provide the Calendly link."*
  * **If `proposal_ready`:** System prompt instructs the AI: *"Confirm their details and state that an email proposal is being sent. Ask for their best email."*
* **`node_human_review` (User Input Step / Breakpoint):** Uses LangGraph's `interrupt()` API to pause the system indefinitely. For example, if the user asks a complex technical question, the AI pauses here until you answer.

### 3. Native Routing via `Command`

We will replace edge-building logic with node-level `Command` returns.

```python
def node_classify(state: AgentState) -> Command[Literal["node_retrieve", "node_human_review", "node_action_pipeline"]]:
    classification = structured_llm.invoke(prompt)
  
    if classification["is_escalated"]:
        return Command(update={"classification": classification}, goto="node_human_review")
    elif "sync_pipeline" in classification["requires_action"]:
        return Command(update={"classification": classification}, goto="node_action_pipeline")
    else:
        return Command(update={"classification": classification}, goto="node_retrieve")
```

## Implementation Phases

1. **Refactor State & Models:** Update [AgentState](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#31-48) and define Pydantic/TypedDict models for LLM structured output focusing on pipeline stage decisions.
2. **Add Checkpointer for Breakpoints:** Add a `PostgresSaver` checkpointer to [build_graph()](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#299-436) to enable human-in-the-loop pauses.
3. **Rewrite Nodes & Implement `Command` Routing:** Split the monolithic logic into distinct nodes (`classify`, `update_status`, [retrieve](file:///home/sh/sajjad/Agentic-communication-system/agentic-sytem-backend/app/services/ai_graph.py#309-331), `draft`, `human_review`). Wire them using LangGraph's `Command` routing.
4. **Implement Stage-Driven Prompts:** Update the `node_draft` logic to drastically change the AI's allowed behavior and instructions based on the current pipeline stage (e.g. unlocking the Calendly link only when `qualified`).
