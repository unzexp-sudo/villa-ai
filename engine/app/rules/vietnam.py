"""Vietnam rule pack (STUB).

QCVN / TCVN rule numbers have NOT been validated by a local engineer, so every
rule returns ``not_checked`` with that reason. Shipping three honest
``not_checked`` rules is the correct behaviour here: pretending coverage we do
not have is exactly the failure the build plan warns about ("report not checked
honestly where a rule can't be evaluated").
"""

from __future__ import annotations

from app.contract.enums import Country, Severity, SystemKind
from app.rules.engine import Rule, _not_checked

_QCVN = "QCVN (unvalidated)"
D = SystemKind.DRAINAGE
A = SystemKind.AIR
P = SystemKind.POWER

_REASON = ("VN rule pack not validated by a local engineer; no QCVN number is "
           "confirmed, so this check is not evaluated")


def _stub(model, layers):
    return _not_checked(R_VN_PLUMBING, _REASON)


def _stub2(model, layers):
    return _not_checked(R_VN_VENT, _REASON)


def _stub3(model, layers):
    return _not_checked(R_VN_ELEC, _REASON)


R_VN_PLUMBING = Rule("QCVN-VN-01", "Plumbing and drainage meet the national QCVN "
                                   "requirements.", _QCVN, (D,), _stub, Severity.WARNING,
                    country=Country.VN)
R_VN_VENT = Rule("QCVN-VN-02", "Ventilation meets the national QCVN requirements.", _QCVN,
                 (A,), _stub2, Severity.WARNING, country=Country.VN)
R_VN_ELEC = Rule("QCVN-VN-03", "Electrical installation meets the national QCVN "
                               "requirements.", _QCVN, (P,), _stub3, Severity.WARNING,
                 country=Country.VN)

RULES: tuple[Rule, ...] = (R_VN_PLUMBING, R_VN_VENT, R_VN_ELEC)

__all__ = ["RULES"]
