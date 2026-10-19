"""The few words slice I prints in documents it builds (the joint statement to sign, the trial pack and the
concordance PDF), keyed by the pack's language. Court words (the meeting's name, the bundle's name, the
declarations) come from the case's packs (rule 11); screens use i18n keys, not these.
"""

from __future__ import annotations

_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "not_in_bundle": "not in the {court_bundle}",
        "trial_page_to_confirm": "trial page to confirm",
        "trial_pack": "Trial pack",
        "report": "Report",
        "chronology": "Chronology of checked facts",
        "readiness": "Readiness sheets",
        "sheet": "Question {number}: {text}",
        "my_answer": "My answer",
        "may_be_asked": "What I may be asked",
        "joint_statement": "{meeting}",
        "written_answers": "Answers to written questions",
        "legal_tests": "Legal tests (reference)",
        "guidance": "Guidance",
        "none_yet": "None yet.",
        "discussion_on": "Discussed on {date}",
        "item": "{number}. {text}",
        "own_view": "My view: {text}",
        "other_views": "Other views: {text}",
        "agreed": "Agreed.",
        "not_agreed": "Not agreed. Reason: {text}",
        "declarations": "Declarations",
        "signed_by": "Signed by: ______________________ Date: __________",
        "participants": "Present: {names}",
        "concordance": "Page numbers in the {court_bundle}",
        "concordance_row": "Page {page_no}: {where}",
        "matched": "{court_bundle} page {trial_page_no}",
        "to_confirm": "{court_bundle} page {trial_page_no} (to confirm)",
        "missing": "not in the {court_bundle}",
        "fact": "{date} page {page}: {text}",
        "undated": "No date",
    },
}


def word(language: str, key: str, **values: object) -> str:
    table = _WORDS.get(language.split("-")[0].lower(), _WORDS["en"])
    return table[key].format(**values)
