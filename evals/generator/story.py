"""The Mrs A story: one invented patient, practice and set of events.

Everything here is fictional. The NHS number uses the 999 range, which is
reserved for testing and never issued to a real person.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from common import nhs_number_valid

PATIENT = {
    "title": "Mrs",
    "first": "Margaret",
    "last": "Ashworth",
    "dob": date(1969, 3, 14),
    "sex": "Female",
    "nhs": "9994471015",
    "address": ["27 Larch Close", "Northam", "NM4 7QT"],
    "phone": "07700 900417",          # Ofcom drama range
    "hospital_numbers": {"northam": "NG4471902", "westvale": "W0882213"},
    "usual_gp": "Dr Rakesh Patel",
}
assert nhs_number_valid(PATIENT["nhs"]), "fixture NHS number must pass mod-11"

PRACTICE = {
    "name": "Brookvale Surgery",
    "address": ["4 Mill Road", "Northam", "NM4 2QT"],
    "ods": "Y99001",
    "phone": "01632 960218",          # Ofcom drama range
}

CLINICIANS = {
    "patel": ("Dr Rakesh Patel", "GP"),
    "okafor": ("Dr Sola Okafor", "GP"),
    "hughes": ("Dr Emma Hughes", "GP Registrar"),
    "nurse": ("Claire Bennett", "Practice Nurse"),
    "hca": ("Tom Riley", "Healthcare Assistant"),
    "pharm": ("Priya Shah", "Clinical Pharmacist"),
    "admin": ("Jo Moss", "Medical Secretary"),
    "locum": ("Dr Alan Greaves", "Locum GP"),
    # Castle Green Practice (SystmOne), registered 2014 to 2016
    "cg_lee": ("Dr Hannah Lee", "GP"),
    "cg_ward": ("Dr Martin Ward", "GP"),
    "cg_nurse": ("Sarah Quinn", "Practice Nurse"),
}

CASTLE_GREEN = {
    "name": "Castle Green Practice",
    "address": ["Castle Green Health Centre", "Eastmere", "EM2 9JD"],
    "ods": "Y99042",
    "patient_address": ["Flat 3, 18 Abbey Street", "Eastmere", "EM2 4RS"],
}

# Names Presidio must strip (case name list)
CASE_NAMES = ["Margaret Ashworth", "Ashworth", "Margaret"]


@dataclass
class Med:
    name: str
    dose: str
    qty: str
    kind: str = "acute"          # acute | repeat


@dataclass
class Consultation:
    d: date
    time: str
    ctype: str                   # Surgery Consultation | Telephone call | Home visit | Administration
    who: str                     # key in CLINICIANS
    problem: str | None = None
    history: str | None = None
    exam: str | None = None
    comment: str | None = None
    plan: str | None = None
    values: list[tuple[str, str]] = field(default_factory=list)   # (label, value)
    meds: list[Med] = field(default_factory=list)
    referral: str | None = None
    safety_net: str | None = None
    planted: dict = field(default_factory=dict)  # field name -> planted item id
    tags: list[str] = field(default_factory=list)


@dataclass
class ResultLine:
    test: str
    value: str
    unit: str
    rng: str
    flag: str = ""               # H | L | ""


@dataclass
class Result:
    requested: date
    reported: date
    filed: date
    filed_by: str
    panel: str
    lines: list[ResultLine]
    comment: str
    lab_no: str
    print_style: str = "uk"      # how the lab prints dates
    planted: str | None = None


# --- Core events (period in question Feb to Sep 2024) ---------------------
CORE_CONSULTS = [
    Consultation(date(2024, 2, 12), "09:40", "Surgery Consultation", "patel",
                 problem="Tiredness symptom",
                 history="Tired all the time for 2 months. Sleeping ok. Mood ok. No weight loss noticed. "
                         "Periods stopped 4 yrs ago. No PMB. Bowels normal per pt.",
                 exam="Looks well. Not pale. BP 138/84. Pulse 78 reg.",
                 plan="Bloods: FBC, ferritin, TFT, U&E, LFT, HbA1c. Review with results.",
                 values=[("Blood pressure", "138/84 mmHg"), ("Pulse rate", "78 /min")]),
    Consultation(date(2024, 3, 4), "11:15", "Telephone call", "patel",
                 problem="Change in bowel habit",
                 history="3 weeks looser stools, 2-3x/day. No blood seen. Some lower abdo cramping. "
                         "Eating ok. Recent bloods discussed - mild anaemia, will recheck.",
                 comment="?IBS ?post viral",
                 plan="Diet advice. Loperamide prn. Stool sample if persists.",
                 meds=[Med("Loperamide 2mg capsules", "Two after first loose stool then one after each loose stool, max 8 a day", "30 capsule")]),
    Consultation(date(2024, 5, 15), "10:20", "Surgery Consultation", "okafor",
                 problem="Tiredness symptom",
                 history="Ongoing tiredness. Bowels still variable, loose some days. Thinks lost a bit of weight, "
                         "clothes looser. No PR bleeding. No vomiting.",
                 exam="Abdo soft, mild LIF tenderness, no masses. PR declined. Wt 64.2kg.",
                 comment="Recent FBC Hb 104. Likely iron deficiency.",
                 plan="Start ferrous fumarate. review 3/52 if no better.",
                 values=[("Body weight", "64.2 kg")],
                 meds=[Med("Ferrous fumarate 210mg tablets", "One tablet three times a day", "84 tablet")],
                 planted={"plan": "7"},
                 tags=["page_118"]),
    Consultation(date(2024, 5, 20), "16:05", "Telephone call", "nurse",
                 problem="Iron deficiency anaemia",
                 history="Pt rang re side effects of iron - constipation. Advised take with food.",
                 safety_net="Advised to call back or 111 if black stools, bleeding from back passage, "
                            "breathless or worsening tiredness.",
                 planted={"safety_net": "10"},
                 tags=["page_121"]),
    Consultation(date(2024, 6, 24), "10:50", "Surgery Consultation", "patel",
                 problem="Change in bowel habit",
                 history="Bowels alternating, some urgency. Tiredness a little better on iron. No bleeding.",
                 exam="Abdo soft non tender. Wt 63.1kg.",
                 plan="Refer gastroenterology (routine) for change in bowel habit and anaemia.",
                 values=[("Body weight", "63.1 kg")],
                 referral="Routine referral to Gastroenterology, Northam General Hospital",
                 planted={"referral": "12"}),
    Consultation(date(2024, 8, 20), "09:10", "Surgery Consultation", "okafor",
                 problem="Rectal bleeding",
                 history="2 episodes fresh red blood mixed with stool this week. Ongoing loose stools. "
                         "Has not heard from gastro.",
                 exam="Abdo soft. PR: no palpable mass, blood on glove. Wt 61.8kg.",
                 plan="2WW referral lower GI today. FIT sent.",
                 values=[("Body weight", "61.8 kg")],
                 referral="Suspected cancer referral (lower GI), Westvale University Hospitals"),
]

CORE_RESULTS = [
    Result(date(2024, 2, 12), date(2024, 2, 14), date(2024, 2, 16), "patel", "Full blood count / Ferritin",
           [ResultLine("Haemoglobin", "112", "g/L", "115 - 165", "L"),
            ResultLine("MCV", "77", "fL", "80 - 100", "L"),
            ResultLine("Platelets", "402", "10*9/L", "150 - 400", "H"),
            ResultLine("Ferritin", "9", "ug/L", "13 - 150", "L")],
           "Noted - recheck 3 months", "NP24-118734", planted="11"),
    Result(date(2024, 5, 10), date(2024, 5, 13), date(2024, 5, 14), "okafor", "Full blood count",
           [ResultLine("Haemoglobin", "104", "g/L", "115 - 165", "L"),
            ResultLine("MCV", "75", "fL", "80 - 100", "L"),
            ResultLine("Platelets", "418", "10*9/L", "150 - 400", "H")],
           "Discuss at appt 15/5", "NP24-201977", planted="9"),
]
