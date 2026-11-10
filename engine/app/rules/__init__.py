"""Rule packs and the code-check engine.

``from app.rules import check_code`` is the only entry point callers need.
"""

from app.rules.engine import Rule, all_rules, check_code, rule_by_id

__all__ = ["Rule", "all_rules", "check_code", "rule_by_id"]
