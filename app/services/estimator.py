"""Rule-based budget/time estimator for early sales guidance."""

from __future__ import annotations

from typing import Any


def _contains_any(text: str, terms: list[str]) -> bool:
    t = (text or "").lower()
    return any(term in t for term in terms)


def estimate_project(project_summary: str, slots: dict[str, Any]) -> dict[str, Any]:
    """Return practical ranges for MVP/standard/advanced builds."""
    text = f"{project_summary} {slots}".lower()
    is_ecommerce = _contains_any(text, ["ecommerce", "e-commerce", "shop", "checkout", "catalog"])
    has_devops = _contains_any(text, ["devops", "infrastructure", "scalable", "deployment"])
    has_dashboard = _contains_any(text, ["dashboard", "analytics", "admin"])
    complexity = "medium"
    if has_devops or has_dashboard:
        complexity = "high"
    if is_ecommerce:
        mvp_budget = "USD 8k-15k"
        std_budget = "USD 18k-35k"
        adv_budget = "USD 40k-75k+"
        mvp_time = "6-10 weeks"
        std_time = "10-16 weeks"
        adv_time = "4-8 months"
    else:
        mvp_budget = "USD 5k-12k"
        std_budget = "USD 12k-25k"
        adv_budget = "USD 25k-60k+"
        mvp_time = "4-8 weeks"
        std_time = "8-14 weeks"
        adv_time = "3-6 months"

    assumptions = [
        "Design + development scope is clear",
        "Content and approvals are provided on time",
        "No major mid-project change requests",
    ]
    if complexity == "high":
        assumptions.append("Includes advanced integrations/infrastructure requirements")

    return {
        "complexity": complexity,
        "mvp_budget_range": mvp_budget,
        "standard_budget_range": std_budget,
        "advanced_budget_range": adv_budget,
        "mvp_timeline": mvp_time,
        "standard_timeline": std_time,
        "advanced_timeline": adv_time,
        "assumptions": assumptions,
    }
