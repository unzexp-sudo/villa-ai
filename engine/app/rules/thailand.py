"""Thailand rule pack (STUB).

Ministerial regulation and EIT standard numbers have not been validated, so
every rule reports ``not_checked`` with that reason rather than guessing.
"""

from __future__ import annotations

from app.contract.enums import Country, Severity, SystemKind
from app.rules.engine import Rule, _not_checked

_EIT = "EIT / ministerial regulation (unvalidated)"
D = SystemKind.DRAINAGE
A = SystemKind.AIR
P = SystemKind.POWER

_REASON = ("TH rule pack not validated by a local engineer; no ministerial "
           "regulation or EIT number is confirmed, so this check is not evaluated")


def _stub(model, layers, rule):
    return _not_checked(rule, _REASON)


def _plumbing(model, layers):
    return _stub(model, layers, R_TH_PLUMBING)


def _vent(model, layers):
    return _stub(model, layers, R_TH_VENT)


def _elec(model, layers):
    return _stub(model, layers, R_TH_ELEC)


R_TH_PLUMBING = Rule("EIT-TH-01", "Sanitary plumbing meets the ministerial requirements.",
                     _EIT, (D,), _plumbing, Severity.WARNING, country=Country.TH)
R_TH_VENT = Rule("EIT-TH-02", "Ventilation and air conditioning meet the ministerial "
                              "requirements.", _EIT, (A,), _vent, Severity.WARNING,
                 country=Country.TH)
R_TH_ELEC = Rule("EIT-TH-03", "Electrical installation meets the ministerial requirements.",
                 _EIT, (P,), _elec, Severity.WARNING, country=Country.TH)

RULES: tuple[Rule, ...] = (R_TH_PLUMBING, R_TH_VENT, R_TH_ELEC)

__all__ = ["RULES"]
