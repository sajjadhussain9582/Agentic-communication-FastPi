# Ops Runbook

## Daily checks

- Review `/api/v1/ops/quality-summary` for:
  - generated message volume,
  - escalations,
  - reminder scheduling count,
  - outbound delivery volume.
- Review `/api/v1/integrations` and `/api/v1/integrations/{provider}/runs` for failures.

## Incident flow

1. If outbound failures increase, inspect recent integration runs for `outbound_*` actions.
2. If escalations spike, inspect conversations with `status=pending_human`.
3. If no generated messages in active hours, verify OpenAI key and webhook ingestion path.

## Weekly quality review

- Sample 20 recent AI replies across personas:
  - contractor
  - agent
  - developer
  - architect
  - builder
- Score:
  - professional tone,
  - connector-only positioning accuracy,
  - CTA clarity,
  - qualification prompt completeness,
  - escalation correctness.
