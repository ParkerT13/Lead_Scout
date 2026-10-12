"""
Fit scoring: collect everyone first, decide who matters afterwards.

classify(title) -> discipline, seniority and a 0-100 "fit" for selling subsurface / E&P
technical software (SeisWare Mercury): geoscientists and reservoir/completions engineers are
the users, VP/Director/Manager over those disciplines are the buyers. Deterministic and free, so
it runs unattended; contacts it cannot place stay in the list as "Unknown" for a human (or a
Claude review of the leftover CSV) to look at.

Nothing here ever removes a contact.
"""

from __future__ import annotations

import re

# (discipline, base score, keyword regex) - first match wins, most specific first
_DISCIPLINES: list[tuple[str, int, re.Pattern]] = [
    ("Geoscience", 100, re.compile(
        r"geolog|geophys|geoscien|petrophys|seismic|stratigraph|geomechan|geomodel|geochem|"
        r"sedimentolog|structural|subsurface|exploration|g&g|g & g|geotech|formation evaluation|paleo", re.I)),
    ("Reservoir/Completions Eng", 90, re.compile(
        r"reservoir|completion|drilling eng|production eng|petroleum eng|well eng|"
        r"unconventional|stimulation|frac|artificial lift|exploitation|development eng", re.I)),
    ("Engineering (other)", 70, re.compile(r"engineer|engineering", re.I)),
    ("Operations", 50, re.compile(
        r"operations|superintendent|foreman|field|production (?:manager|supervisor|lead)|"
        r"facilit|hse|safety|environmental|pumper|gauger|asset", re.I)),
    ("Land", 35, re.compile(r"landman|\bland\b|lease|title|right[- ]of[- ]way|acquisition|divestiture", re.I)),
    ("Data/IT", 45, re.compile(r"\bgis\b|data (?:manager|analyst|scientist)|\bit\b|information technology|"
                               r"software|developer|analytics|database|geomatics", re.I)),
    ("Business", 25, re.compile(r"business development|sales|marketing|commercial|strategy|"
                                r"investor relations|planning|reserves|evaluation", re.I)),
    ("Finance/Admin", 10, re.compile(
        r"financ|account|controller|treasur|\bcfo\b|tax|audit|legal|counsel|attorney|human resources|"
        r"\bhr\b|recruit|payroll|procurement|supply chain|administrat|assistant|secretary|receptionist", re.I)),
]

# Seniority: 1 = top of the company, 6 = individual contributor, 9 = unknown
_SENIORITY: list[tuple[int, re.Pattern]] = [
    (1, re.compile(r"\bchief\b|\bc[eftoi]o\b|(?<!vice )(?<!vice-)president(?! of)|founder|owner|managing (?:partner|director)|chairman|\bceo\b", re.I)),
    (2, re.compile(r"\b(?:e|s)?vp\b|vice president|vice-president", re.I)),
    (3, re.compile(r"director|head of|general manager|principal|\bpartner\b", re.I)),
    (4, re.compile(r"manager|team lead|\blead\b|supervisor|superintendent|chief engineer|chief geolog", re.I)),
    (5, re.compile(r"senior|\bsr\b|\bstaff\b|advis|specialist|consultant", re.I)),
    (6, re.compile(r"engineer|geolog|geophys|geoscien|analyst|technician|coordinator|associate|"
                   r"scientist|landman|accountant|developer|assistant|intern", re.I)),
]
_SENIORITY_LABELS = {1: "C-Suite/Owner", 2: "VP", 3: "Director", 4: "Manager/Lead", 5: "Senior IC",
                     6: "Individual", 9: ""}

_FORMER_RE = re.compile(r"\b(former|formerly|retired|retiree|ex-|past|emeritus|seeking|open to work)\b", re.I)

_TECHNICAL = {"Geoscience", "Reservoir/Completions Eng", "Engineering (other)", "Data/IT"}


def classify(title: str) -> dict:
    """{'discipline', 'seniority', 'seniority_label', 'fit_score', 'fit'} for a job title."""
    t = (title or "").strip()
    if not t:
        return {"discipline": "Unknown", "seniority": 9, "seniority_label": "", "fit_score": 40, "fit": "Medium"}
    discipline, base = "Other", 30
    for name, score, rx in _DISCIPLINES:
        if rx.search(t):
            discipline, base = name, score
            break
    seniority = 9
    for rank, rx in _SENIORITY:
        if rx.search(t):
            seniority = rank
            break
    score = base
    # Decision makers over technical teams are the buyers; executives with no technical word are less so
    if discipline in _TECHNICAL:
        score += {1: -5, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 9: 0}.get(seniority, 0)
    elif seniority in (1, 2, 3) and discipline == "Other":
        score = 45                              # "Vice President" with no discipline: worth a look
    if discipline == "Operations" and seniority in (2, 3):
        score += 10
    former = bool(_FORMER_RE.search(t))
    if former:
        score = min(score, 20)                  # no longer there (or not working): keep, but never High
    score = max(0, min(100, score))
    fit = "High" if score >= 75 else "Medium" if score >= 40 else "Low"
    return {"discipline": discipline, "seniority": seniority, "former": former,
            "seniority_label": _SENIORITY_LABELS.get(seniority, ""), "fit_score": score, "fit": fit}


def annotate(contact: dict) -> dict:
    """Add discipline / fit fields to a contact dict in place (and return it)."""
    c = classify(contact.get("title", ""))
    contact["discipline"] = c["discipline"]
    contact["fit"] = c["fit"]
    contact["fit_score"] = c["fit_score"]
    contact["priority"] = (f"{c['seniority']} - {c['seniority_label']}" if c["seniority_label"] else "")
    return contact
