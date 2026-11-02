"""Build the Mrs A synthetic bundle (450 pages) and its expected answers.

Usage: python evals/generator/build.py OUT_DIR

Writes:
  OUT_DIR/bundles/synthetic-mrs-a/records.pdf      the bundle as a solicitor might send it
  OUT_DIR/expected/*.json                          answers the tests compare against
  OUT_DIR/bundles/synthetic-mrs-a/layout/          where the words are on scanned pages (for recordings.py):
      <name>-source.pdf   page n is page n's text before it was scanned (blank when it never had any)
      <name>.json         [{page, words: [{text, poly (inches), handwritten, struck}]}] drawn on page images

Page numbers in expected files are positions in records.pdf (1-based), which
is also the permanent bundle page number Avodeck assigns in upload order.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
from dataclasses import asdict
from datetime import date, timedelta

from pypdf import PdfReader, PdfWriter

sys.path.insert(0, os.path.dirname(__file__))

import layout  # noqa: E402

layout.install()          # before anything is drawn: note where every word on a page image goes

from common import Doc, Fact, PageInfo, W, H  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from gp_prints import EmisPrint, SystmOnePrint  # noqa: E402
from hospital import (northam_letter, westvale_clinic_letter, eastmere_discharge, lab_report,  # noqa: E402
                      ooh_report, suspected_cancer_form)
from more_formats import (typewriter_letter, radiology_report, histology_report, ed_letter, nhs111_report,  # noqa: E402
                          screening_letter, pharmacy_note, optician_letter, fit_note, ecg, fax_cover,
                          kingsmead_letter, riverside_letter, westvale_path, docman_header_overlay)
from scan import A4_INCHES, scan_pdf, lloyd_george_card, degrade, images_to_pdf  # noqa: E402
from story import PATIENT, CORE_CONSULTS, CORE_RESULTS, CASTLE_GREEN, CASE_NAMES  # noqa: E402
from content import brookvale_background, castle_green_entries, lloyd_george_cards  # noqa: E402
from part2 import all_part2  # noqa: E402
from series import westvale_series, northam_series, private_and_community, handwritten_series, maternity_series  # noqa: E402

TOTAL_PAGES = 450          # part 1 (planted items fixed here)
PART2_PAGES = 450          # pages 451-900: detailed records
TARGETS = {"7": 118, "11_print": 402}      # planted item -> bundle page
N_DUPLICATES = 37
RNG = random.Random(2024)
GP_RESULTS = []            # filled by gp_section; printed lab copies in part 2 use it
PRINTED = date(2025, 10, 7)


class Item:
    def __init__(self, doc=None, quality=None, group="", pages=None, pdf=None, born_digital=None, tag=None,
                 overlay=None, unreadable=False):
        self.doc, self.quality, self.group, self.tag = doc, quality, group, tag
        self.pdf = pdf
        self.pages: list[PageInfo] = pages if pages is not None else (doc.pages if doc else [])
        self.born_digital = (quality is None) if born_digital is None else born_digital
        self.overlay = overlay
        self.unreadable = unreadable
        self.notes = None           # {page_index: [(text, x_frac, y_frac)]} handwritten on paper before scanning
        self.dup_of = None          # (item, page index) for duplicate copies

    @property
    def n(self):
        return len(self.pages)


# ============================================================ GP records (Brookvale, EMIS style)
def gp_section():
    bg_consults, bg_results = brookvale_background()
    consults = sorted(bg_consults + CORE_CONSULTS, key=lambda c: (c.d, c.time))
    results = sorted(bg_results + CORE_RESULTS, key=lambda r: r.reported)
    GP_RESULTS[:] = results
    items = []

    b = EmisPrint("gp-emis-brief-summary", "Brief Summary", PRINTED, "Medical Records (J Moss)")
    b.patient_details(date(2016, 9, 12))
    b.problems(active=[(date(2012, 6, 18), "Essential hypertension", date(2024, 1, 22)),
                       (date(2001, 11, 2), "Hypothyroidism", date(2023, 11, 6)),
                       (date(2024, 9, 12), "Adenocarcinoma of sigmoid colon", date(2024, 12, 9)),
                       (date(2024, 5, 15), "Iron deficiency anaemia", date(2024, 5, 20))],
               sig_past=[(date(2019, 3, 12), "Laparoscopic cholecystectomy", date(2019, 3, 14)),
                         (date(2024, 10, 21), "Anterior resection of rectum and sigmoid", date(2024, 10, 26))],
               minor_past=None)
    b.medication([
        {"drug": "Amlodipine 5mg tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 11, 28), "type": "Repeat"},
        {"drug": "Levothyroxine sodium 75microgram tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 11, 28), "type": "Repeat"},
        {"drug": "Ferrous fumarate 210mg tablets", "dose": "One tablet three times a day", "qty": "84 tablet", "last": date(2024, 10, 3), "type": "Repeat"},
        {"drug": "Ondansetron 4mg tablets", "dose": "One tablet twice a day when required", "qty": "20 tablet", "last": date(2024, 12, 9), "type": "Acute"},
    ])
    b.allergies(["No known drug allergy   (recorded 12-Sep-2016)"])
    b.values([(date(2024, 8, 20), "Body weight", "61.8 kg"), (date(2024, 2, 12), "Blood pressure", "138/84 mmHg"),
              (date(2023, 11, 6), "Smoking status", "Never smoked tobacco"), (date(2023, 11, 6), "Alcohol intake", "4 units/week")])
    items.append(Item(b.doc, None, "gp", tag="emis_brief"))

    f = EmisPrint("gp-emis-full-summary", "Full Summary", PRINTED, "Medical Records (J Moss)")
    f.patient_details(date(2016, 9, 12))
    f.problems(active=[(date(2012, 6, 18), "Essential hypertension", date(2024, 1, 22)),
                       (date(2001, 11, 2), "Hypothyroidism", date(2023, 11, 6)),
                       (date(2024, 9, 12), "Adenocarcinoma of sigmoid colon", date(2024, 12, 9))],
               sig_past=[(date(2019, 3, 12), "Laparoscopic cholecystectomy", date(2019, 3, 14))],
               minor_past=[(date(2021, 2, 8), "Knee pain", date(2021, 6, 1)), (date(2022, 7, 15), "Ankle sprain", date(2022, 8, 1))])
    f.medication([
        {"drug": "Amlodipine 5mg tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 11, 28), "type": "Repeat"},
        {"drug": "Levothyroxine sodium 75microgram tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 11, 28), "type": "Repeat"},
    ])
    f.allergies(["No known drug allergy   (recorded 12-Sep-2016)"])
    f.consultations_heading()
    docs_filed = gp_document_entries()
    entries = [("c", c.d, c) for c in consults] + [("r", r.reported, r) for r in results] + \
              [("d", x[0], x) for x in docs_filed]
    entries.sort(key=lambda t: t[1], reverse=True)
    for kind, _, obj in entries:
        if kind == "c":
            f.consultation(obj)
        elif kind == "r":
            f.result(obj)
        else:
            f.document_entry(*obj)
    items.append(Item(f.doc, "scan", "gp", tag="emis_full"))

    m = EmisPrint("gp-emis-medication-issues", "Medication Issues", PRINTED, "Medical Records (J Moss)")
    issues = []
    d = date(2003, 10, 6)
    while d < date(2024, 12, 1):
        if date(2014, 9, 20) < d < date(2016, 9, 12):
            d += timedelta(days=28)
            continue
        for drug, dose in [("Amlodipine 5mg tablets", "One tablet once a day"),
                           ("Levothyroxine sodium 75microgram tablets", "One tablet once a day")]:
            issues.append({"d": d, "drug": drug, "dose": dose, "qty": "28 tablet", "by": RNG.choice(["Dr R Patel", "Dr S Okafor", "P Shah"])})
        if date(2019, 10, 7) <= d < date(2024, 9, 1):
            issues.append({"d": d, "drug": "Estradiol 0.06% gel", "dose": "Two pumps daily", "qty": "80 gram", "by": "Dr S Okafor"})
        if date(2024, 5, 15) <= d:
            issues.append({"d": d, "drug": "Ferrous fumarate 210mg tablets", "dose": "One tablet three times a day", "qty": "168 tablet", "by": "Dr S Okafor"})
        d += timedelta(days=28)
    issues.sort(key=lambda i: i["d"], reverse=True)
    m.issues(issues)
    items.append(Item(m.doc, "scan", "gp", tag="emis_issues"))

    a = EmisPrint("gp-emis-appointments", "Appointment History", PRINTED, "Medical Records (J Moss)")
    appts = []
    for c in consults:
        if c.d >= date(2016, 9, 12) and c.ctype != "Administration":
            appts.append({"d": c.d, "t": c.time, "slot": {"Telephone call": "Telephone appointment", "Home visit": "Home visit",
                                                           "Video consultation": "Video"}.get(c.ctype, "Routine 10 min"),
                          "with": __import__("story").CLINICIANS[c.who][0], "outcome": "Attended"})
    appts += [{"d": date(2023, 10, 16), "t": "08:40", "slot": "Practice nurse - smear recall", "with": "Claire Bennett", "outcome": "Did not attend"},
              {"d": date(2024, 6, 3), "t": "09:20", "slot": "Routine 10 min (review 3/52)", "with": "Dr Sola Okafor", "outcome": "Cancelled by practice"},
              {"d": date(2022, 2, 14), "t": "15:10", "slot": "Routine 10 min", "with": "Dr Rakesh Patel", "outcome": "Did not attend"}]
    appts.sort(key=lambda x: x["d"], reverse=True)
    a.appointments(appts)
    items.append(Item(a.doc, "scan", "gp", tag="emis_appts"))

    t = EmisPrint("gp-emis-tasks", "Tasks", PRINTED, "Medical Records (J Moss)")
    tasks = []
    d = date(2016, 10, 10)
    descs = ["Repeat prescription request - authorise", "Hospital letter to review", "Result to file", "Recall for BP review",
             "Patient query - call back", "Insurance report request", "Update medication after clinic letter"]
    while d <= date(2024, 1, 29):   # planted gap: tasks stop at the end of January 2024
        for _ in range(RNG.randint(1, 3)):
            tasks.append({"d": d + timedelta(days=RNG.randint(0, 20)), "type": RNG.choice(["Admin", "Clinical", "Prescribing"]),
                          "desc": RNG.choice(descs), "to": RNG.choice(["Dr R Patel", "Dr S Okafor", "Reception", "P Shah"]),
                          "status": "Completed"})
        d += timedelta(days=30)
    tasks = [x for x in tasks if x["d"] <= date(2024, 1, 31)]
    tasks.sort(key=lambda x: x["d"], reverse=True)
    t.tasks(tasks)
    items.append(Item(t.doc, "scan", "gp", tag="emis_tasks"))
    return items


def gp_document_entries():
    """Docman-style document entries in the EMIS journal: (when, title, source, filed_by, filed_on, comment)."""
    return [
        (date(2024, 5, 22), "Haematology Advice and Guidance response (letter dated 10/05/2024)", "Northam General Hospital",
         "Dr Rakesh Patel", date(2024, 5, 28), "Noted - discuss at review"),
        (date(2024, 3, 31), "Out of hours case report NUC-24-033187", "Northam Urgent Care", "Dr Rakesh Patel",
         date(2024, 4, 2), "Seen OOH, advised see GP"),
        (date(2024, 9, 4), "Gastroenterology appointment letter (dated 30/08/2024)", "Northam General Hospital",
         "Jo Moss", date(2024, 9, 4), "Filed"),
        (date(2024, 9, 16), "Colorectal clinic letter 12/09/2024", "Westvale University Hospitals", "Dr Sola Okafor",
         date(2024, 9, 16), "Noted. Pt contacted."),
        (date(2023, 12, 4), "Bowel cancer screening - kit not returned", "Bowel Cancer Screening Programme", "Jo Moss",
         date(2023, 12, 4), "Filed - no action"),
        (date(2019, 3, 18), "Discharge summary - laparoscopic cholecystectomy", "Eastmere Hospitals", "Dr Rakesh Patel",
         date(2019, 3, 20), "Clips removal booked"),
        (date(2021, 3, 29), "Orthopaedic letter (private)", "Kingsmead Private Hospital", "Dr Rakesh Patel",
         date(2021, 3, 30), "Noted"),
    ]


# ============================================================ GP correspondence (attachments held by practice)
def gp_correspondence():
    out = []
    out.append(Item(lab_report("gpc-lab-tft-2024", collected=date(2024, 4, 1), reported=date(2024, 4, 3), requester="DR R PATEL",
                               lab_no="NP24-150022", lines=[("TSH", "2.1", "mU/L", "0.27 - 4.2", ""),
                                                            ("Free T4", "15.8", "pmol/L", "12 - 22", "")],
                               comment="Thyroid function within reference range.",
                               facts=[Fact("result", "2.1 mU/L", "TSH 2.1 mU/L", "2024-04-03", {"test": "TSH"}, "6")]),
                    "fax", "gpc"))
    out.append(Item(screening_letter("gpc-bowel-screen-2023", programme="NHS Bowel Cancer Screening Programme",
                                     dated=date(2023, 11, 27), result="We have not received your completed test kit",
                                     body=["We sent you a home test kit for bowel cancer screening on 9 October 2023. "
                                           "We have not yet received your completed kit.",
                                           "If you would like a replacement kit please call the free helpline. "
                                           "If you have symptoms such as bleeding from your bottom or a change in bowel habit, "
                                           "please see your GP rather than waiting for screening."],
                                     sender="Midlands and North West Hub (invented)"), "scan", "gpc"))
    for yr, res in [(2017, "No HPV found"), (2020, "No HPV found"), (2023, "No HPV found")]:
        out.append(Item(screening_letter(f"gpc-cervical-{yr}", programme="NHS Cervical Screening Programme",
                                         dated=date(yr, 4, 3), result=f"Your cervical screening result: {res}",
                                         body=["Your recent cervical screening test did not find human papillomavirus (HPV).",
                                               "This means your risk of cervical cancer is very low. We will invite you again "
                                               "in 5 years."], sender="Cervical Screening Administration Service"),
                        RNG.choice(["scan", None]), "gpc"))
    for yr in (2018, 2021, 2024):
        out.append(Item(screening_letter(f"gpc-breast-{yr}", programme="NHS Breast Screening Programme",
                                         dated=date(yr, 1, 22), result="Your breast screening result: normal",
                                         body=["We are pleased to tell you that your recent mammogram (breast X-ray) "
                                               "showed no sign of cancer.", "You will be invited again in 3 years."],
                                         sender="Northam Breast Screening Unit"), "scan", "gpc"))
    out.append(Item(pharmacy_note("gpc-pharmacy-2023", dated=date(2023, 2, 6), pharmacy="Mill Road Pharmacy (CPCS referral)",
                                  reason="Sore throat 2 days", advice="FeverPAIN 1. Self care advice.", supplied="Lozenges (OTC)"),
                    "scan", "gpc"))
    out.append(Item(optician_letter("gpc-optician-2022", dated=date(2022, 9, 12),
                                    findings="Routine sight test. Intraocular pressures R 23 L 22 mmHg, optic discs healthy, "
                                             "visual fields full.",
                                    request="Pressures borderline. I have advised a repeat in 12 months; no referral needed at present."),
                    "scan", "gpc"))
    out += practice_letters()
    out.append(Item(fit_note("gpc-fitnote-2024", dated=date(2024, 12, 9), condition="Bowel cancer - on chemotherapy",
                             weeks=8, clinician="Dr S Okafor"), None, "gpc"))
    out.append(Item(nhs111_report("gpc-111-2023", call_date=date(2023, 8, 19), time="21:14",
                                  symptoms="Dizziness / vertigo", disposition="Speak to own GP practice within 24 hours",
                                  advice="Sit or lie down when dizzy. Call back if new weakness or slurred speech.",
                                  sent=date(2023, 8, 19)), None, "gpc"))
    out.append(Item(ooh_report("gpc-ooh-2024", case_no="NUC-24-033187", received_at="14:22", call_date=date(2024, 3, 30),
                               clinician="Dr M Fenwick (OOH GP)",
                               presenting="Lower abdominal pain and loose stools for 4 weeks, worse today.",
                               notes="Obs: T 37.1, HR 84, BP 132/80. Abdo soft, mild lower tenderness, no guarding. "
                                     "No PR bleeding reported. Advised fluids, analgesia.",
                               outcome="Advised to see own GP next week for review and further investigation if not settling. "
                                       "Worsening advice given.",
                               sent_to_gp=date(2024, 3, 31), sent_time="08:02",
                               facts=[Fact("consultation", "Lower abdominal pain and loose stools for 4 weeks",
                                           "Lower abdominal pain and loose stools for 4 weeks", "2024-03-30",
                                           {"setting": "out_of_hours"}, "13")]), "scan", "gpc"))
    out.append(Item(suspected_cancer_form("gpc-2ww-2024", referred=date(2024, 8, 20), gp="Dr S Okafor",
                                          criteria_ticked=["Aged 40 and over with unexplained weight loss and abdominal pain",
                                                           "Rectal bleeding with change in bowel habit"],
                                          all_criteria=["Aged 40 and over with unexplained weight loss and abdominal pain",
                                                        "Aged 50 and over with unexplained rectal bleeding",
                                                        "Aged 60 and over with iron-deficiency anaemia or change in bowel habit",
                                                        "Rectal bleeding with change in bowel habit", "Abdominal or rectal mass",
                                                        "Positive FIT (faecal haemoglobin 10 ug Hb/g or more)"],
                                          history="55F. Tiredness since Feb 2024, microcytic anaemia (ferritin 9), loose stools, "
                                                  "weight 64.2kg (May) to 61.8kg (Aug). Fresh red blood PR x2 this week. "
                                                  "PR: no mass, blood on glove.",
                                          fit="Sent 20/08/2024 - result awaited",
                                          facts=[Fact("referral", "Suspected lower gastrointestinal cancer referral",
                                                      "SUSPECTED LOWER GASTROINTESTINAL CANCER REFERRAL", "2024-08-20")]),
                    "scan", "gpc"))
    # the printed lab copy of the February ferritin, filed in the paper attachments (planted 11, at page 402)
    out.append(Item(lab_report("gpc-lab-ferritin-2024", collected=date(2024, 2, 12), reported=date(2024, 2, 14),
                               requester="DR R PATEL", lab_no="NP24-118734",
                               lines=[("Haemoglobin", "112", "g/L", "115 - 165", "L"), ("MCV", "77", "fL", "80 - 100", "L"),
                                      ("Platelets", "402", "10*9/L", "150 - 400", "H"), ("Ferritin", "9", "ug/L", "13 - 150", "L")],
                               comment="Low ferritin consistent with iron deficiency.",
                               facts=[Fact("result", "9 ug/L", "Ferritin 9 ug/L", "2024-02-14", {"test": "Ferritin"}, "11"),
                                      Fact("result", "112 g/L", "Haemoglobin 112 g/L", "2024-02-14", {"test": "Haemoglobin"}, "11")]),
                    "scan", "gpc", tag="ferritin_print"))
    return out


# ============================================================ Lloyd George envelope
def lloyd_george_items():
    out = []
    for i, (entries, messy) in enumerate(lloyd_george_cards()):
        img = lloyd_george_card(entries, PATIENT, seed=300 + i, messy=messy)
        pi = PageInfo(f"lg-card-{i + 1:02d}", 1, "gp_record")
        for when, lines in entries:
            pi.facts.append(Fact("consultation", lines[0], lines[0], when.isoformat(), {"handwritten": True, "all_lines": lines}))
            pi.printed_dates.append(when.isoformat())
        out.append(Item(None, "scan", "lg", pages=[pi], pdf=("image", img), born_digital=False, unreadable=messy >= 0.8))
    old = [
        (date(1991, 6, 4), "Northam Health Authority", "Department of Gynaecology", "Re: Margaret Ashworth dob 14.3.69 - "
         "menorrhagia", ["I saw this 22 year old lady in clinic with heavy periods. Examination was normal. "
                         "Haemoglobin 118. I have suggested tranexamic acid and will review in 4 months."], "Mr P Dunn", "Consultant Gynaecologist"),
        (date(1998, 9, 21), "Northam Health Authority", "Department of Medicine", "Re: Margaret Ashworth - abnormal thyroid",
         ["Thank you for referring this lady with tiredness and a raised TSH of 8.9. Thyroid antibodies are positive. "
          "This is consistent with autoimmune hypothyroidism. I agree with thyroxine, titrating to a normal TSH. "
          "No follow up arranged."], "Dr K Sharma", "Consultant Endocrinologist"),
        (date(2001, 7, 12), "Northam General Hospital", "Accident and Emergency", "Re: Margaret Ashworth - left knee injury",
         ["Attended after a fall. X-ray left knee showed no fracture. Discharged with analgesia and advice."],
         "Dr S Bell", "SHO A&E"),
    ]
    for i, (d, org, dept, re_line, body, sign, role) in enumerate(old):
        doc = typewriter_letter(f"lg-old-letter-{i + 1}", org=org, org_lines=[dept, "Hospital Road, Northam"],
                                dated=d, to_lines=["Dr J Carr", "Hollins Lane Medical Centre", "Northam"],
                                re_line=re_line, body=body, sign=sign, role=role)
        out.append(Item(doc, "poor", "lg"))
    return out


def castle_green_item():
    s = SystmOnePrint("gp-castle-green-systmone", date(2025, 9, 30), "Castle Green records team", practice=CASTLE_GREEN,
                      lab_name="Eastmere Pathology")
    from content import routine_results
    for i, c in enumerate(castle_green_entries()):
        s.consultation(c)
        if i % 3 == 1:
            r = routine_results(c.d, 300000 + i * 917)
            r.filed_by = "cg_lee"
            r.lab_no = r.lab_no.replace("NP", "EP")
            s.result(r)
    return Item(s.doc, "scan", "cg")


def practice_letters():
    """Letters the practice sent to the patient, plus an insurance report (kept in the GP record)."""
    out = []
    r = random.Random(55)
    kinds = [
        ("Your recent blood test results", ["Dr {gp} has reviewed your recent blood tests and they are satisfactory. "
                                            "No further action is needed."]),
        ("Invitation: annual blood pressure review", ["Our records show you are due your annual blood pressure review. "
                                                       "Please book with the practice nurse."]),
        ("Invitation: NHS Health Check", ["You are invited for a free NHS Health Check. The check takes about 20 minutes."]),
        ("Medication review due", ["Your repeat medicines are due for review. Please book a telephone appointment "
                                   "with our clinical pharmacist."]),
        ("Flu vaccination clinic", ["Our Saturday flu clinics are now open. You are eligible for a free vaccine."]),
    ]
    yrs = [2017, 2018, 2018, 2019, 2020, 2020, 2021, 2022, 2022, 2023, 2023, 2024]
    for i, yr in enumerate(yrs):
        title, body = kinds[i % len(kinds)]
        d = date(yr, r.randint(1, 12), r.randint(1, 12))
        doc = Doc(f"gpc-practice-letter-{i}", "letters", margin_l=24 * mm, margin_r=24 * mm)
        doc.c.setFont("Sans-B", 13)
        doc.c.drawString(doc.ml, H - 22 * mm, "Brookvale Surgery")
        doc.c.setFont("Sans", 8.5)
        doc.c.drawString(doc.ml, H - 27 * mm, "4 Mill Road, Northam NM4 2QT  -  01632 960218")
        doc.y = H - 44 * mm
        for ln in [f"{PATIENT['title']} {PATIENT['first']} {PATIENT['last']}", *PATIENT["address"]]:
            doc.text(doc.ml, ln, "Sans", 10)
        doc.y -= 10
        doc.text(doc.ml, d.strftime("%d %B %Y"), "Sans", 10, lead=18)
        doc.printed_date(d)
        doc.text(doc.ml, f"Dear {PATIENT['title']} {PATIENT['last']}", "Sans", 10.5, lead=16)
        doc.text(doc.ml, title, "Sans-B", 11, lead=18)
        for para in body:
            doc.para(doc.ml, para.format(gp=r.choice(["Patel", "Okafor"])), W - doc.ml - doc.mr, "Sans", 10.5, lead=14)
        doc.y -= 14
        doc.text(doc.ml, "Yours sincerely, Brookvale Surgery", "Sans", 10.5)
        doc.fact("letter", title, event_date=d)
        out.append(Item(doc, r.choice(["scan", None]), "gpc"))
    ins = Doc("gpc-insurance-report-2017", "letters", margin_l=20 * mm, margin_r=20 * mm)
    ins.text(ins.ml, "GENERAL PRACTITIONER'S REPORT (GPR) - LIFE INSURANCE APPLICATION", "Sans-B", 12, lead=18)
    ins.text(ins.ml, "Insurer: Meridian Life (invented)   Ref: ML-77120-AS   Completed 14/06/2017 by Dr R Patel", "Sans", 9, lead=16)
    ins.printed_date(date(2017, 6, 14))
    qa = [("Length of time patient known to practice", "Registered 2003-2014 and since September 2016."),
          ("Current conditions", "Essential hypertension, well controlled on amlodipine 5 mg. Hypothyroidism on levothyroxine 75 micrograms."),
          ("Most recent blood pressure readings", "05/2017 132/80; 11/2016 136/84; 05/2016 (previous practice) 138/86."),
          ("Height / weight / BMI", "165 cm, 68 kg, BMI 25.0 (05/2017)."),
          ("Smoking and alcohol", "Never smoked. Alcohol about 4 units per week."),
          ("Any investigations in last 5 years", "Routine bloods only, all satisfactory. No referrals to hospital."),
          ("Any history of cancer, heart disease, diabetes, stroke", "No."),
          ("Mental health", "Episode of work-related stress noted, no treatment."),
          ("Any symptoms awaiting investigation", "None."),
          ("Family history", "Father myocardial infarction aged 68."),
          ("Anything else the insurer should know", "Nothing further.")]
    for q, a in qa * 2 if False else qa:
        ins.text(ins.ml, q, "Sans-B", 10)
        ins.para(ins.ml + 5 * mm, a, W - ins.ml - ins.mr - 5 * mm, "Sans", 10, lead=13)
        ins.y -= 8
    ins.y -= 10
    ins.text(ins.ml, "I confirm the information given is accurate according to the records held.", "Sans-I", 10)
    ins.text(ins.ml, "Signed: R Patel   GMC 4412907 (invented)   Date 14/06/2017", "Sans", 10)
    ins.fact("letter", "General practitioner's report for life insurance", event_date=date(2017, 6, 14))
    out.append(Item(ins, "scan", "gpc"))
    return out


# ============================================================ Hospital records
def northam_items():
    out = []
    out.append(Item(northam_letter("ng-haem-advice-2024", dept="Department of Haematology", author="Dr J Whitfield",
                                   author_role="Consultant Haematologist", dated=date(2024, 5, 10), dictated=date(2024, 5, 8),
                                   typed=date(2024, 5, 10), received=date(2024, 5, 22), title="Advice and Guidance response",
                                   paragraphs=["Thank you for your Advice and Guidance request regarding this 55 year old lady with "
                                               "a microcytic anaemia (Hb 112 g/L, MCV 77 fL) and ferritin of 9 ug/L in February.",
                                               "Iron deficiency anaemia in a post-menopausal woman should be regarded as "
                                               "gastrointestinal in origin until proven otherwise. I would recommend a FIT test and "
                                               "referral on the suspected lower GI cancer pathway if positive, or upper and lower GI "
                                               "investigation in any case. Coeliac serology would also be reasonable.",
                                               "Oral iron replacement is appropriate in the meantime but should not delay investigation."],
                                   gp_actions=["Arrange FIT and coeliac serology", "Refer for GI investigation"],
                                   facts=[Fact("letter", "Advice and Guidance response", "Advice and Guidance response",
                                               "2024-05-10", {"specialty": "Haematology"}, "8")]),
                    "scan", "ng", tag="haem_letter"))
    out.append(Item(northam_letter("ng-gastro-appt-2024", dept="Department of Gastroenterology", author="Appointments Office",
                                   author_role="On behalf of Dr F Rahman, Consultant Gastroenterologist", dated=date(2024, 8, 30),
                                   dictated=date(2024, 8, 30), typed=date(2024, 8, 30), received=None,
                                   title="Referral received - appointment offered",
                                   paragraphs=["Your referral dated 24/06/2024 has been triaged as routine. An appointment has been "
                                               "offered for 14/10/2024 at 10:30 in Gastroenterology Clinic B.",
                                               "Current routine waiting time is approximately 14 weeks."],
                                   facts=[Fact("letter", "Referral received - appointment offered",
                                               "Referral received - appointment offered", "2024-08-30",
                                               {"acknowledges_referral_dated": "2024-06-24"}, "12")]),
                    "scan", "ng"))
    out.append(Item(northam_letter("ng-chest-pain-2018", dept="Rapid Access Chest Pain Clinic", author="Dr C Morgan",
                                   author_role="Consultant Cardiologist", dated=date(2018, 4, 23), dictated=date(2018, 4, 23),
                                   typed=date(2018, 4, 25), received=date(2018, 4, 30), title="Rapid access chest pain clinic",
                                   diagnosis=["Non-cardiac chest pain", "Hypertension"],
                                   paragraphs=["I saw Mrs Ashworth with a three week history of exertional chest tightness. Her ECG "
                                               "showed sinus rhythm with no ischaemic changes. CT coronary angiogram showed a calcium "
                                               "score of zero with no coronary stenosis.",
                                               "I do not think her symptoms are cardiac. I have discharged her back to your care."]),
                    "scan", "ng"))
    out.append(Item(ecg("ng-ecg-2018", recorded=date(2018, 4, 23), time="10:42", rate=74, pr=156, qrs=86, qt=376, qtc=410,
                        interp="Sinus rhythm. Normal ECG.", where="RACPC Northam"), "scan", "ng"))
    out.append(Item(radiology_report("ng-xr-knee-2021", trust="Northam General Hospital", style="plain", exam="XR Right knee",
                                     requested=date(2021, 2, 8), performed=date(2021, 2, 15), reported=date(2021, 2, 16),
                                     clinical="R knee pain 2 months, ?OA", findings="Mild medial joint space narrowing. Small "
                                     "osteophytes. No effusion. No fracture.", conclusion="Mild medial compartment osteoarthritis.",
                                     radiologist="Dr H Iqbal, Consultant Radiologist", accession="NGR2100871"), "scan", "ng"))
    out.append(Item(radiology_report("ng-us-abdo-2018", trust="Northam General Hospital", style="plain",
                                     exam="US Abdomen", requested=date(2018, 11, 12), performed=date(2018, 12, 3),
                                     reported=date(2018, 12, 3), clinical="RUQ pain after fatty food",
                                     findings="Multiple gallstones in a thin walled gallbladder. CBD 4 mm. Liver, pancreas, "
                                              "kidneys normal.", conclusion="Cholelithiasis. No cholecystitis.",
                                     radiologist="Dr H Iqbal, Consultant Radiologist", accession="NGR1806652"), "scan", "ng"))
    out.append(Item(northam_letter("ng-bp-2012", dept="Department of Medicine", author="Dr A Rowe",
                                   author_role="Consultant Physician", dated=date(2012, 7, 2), dictated=date(2012, 7, 2),
                                   typed=date(2012, 7, 6), received=date(2012, 7, 11), title="Hypertension clinic",
                                   paragraphs=["Ambulatory BP average 148/94. Renal function and urinalysis normal. "
                                               "I agree with amlodipine 5mg daily."]), "poor", "ng"))
    # GPs annotate letters by hand when they read them
    haem = out[0]
    haem.notes = {0: [("Seen RP 28/5/24 - discuss at review", 0.55, 0.80)]}
    haem.pages[0].availability.append({"kind": "seen", "date": "2024-05-28", "quote": "Seen RP 28/5/24", "handwritten": True})
    out[1].notes = {0: [("filed JM 4/9", 0.62, 0.86)]}
    return out


def westvale_items():
    out = []
    out.append(Item(westvale_clinic_letter("wv-colorectal-2024-09", specialty="Colorectal Surgery", consultant="Mr D Achebe",
                                           seen=date(2024, 9, 12), typed=date(2024, 9, 13),
                                           diagnosis=["Adenocarcinoma of the sigmoid colon (biopsy confirmed)", "Iron deficiency anaemia"],
                                           outcome="Staging CT arranged. For discussion at colorectal MDT 19/09/2024.",
                                           plan=["CT chest, abdomen and pelvis", "Pre-operative assessment",
                                                 "Clinical nurse specialist contact given"],
                                           investigations=["Colonoscopy 02/09/2024: see separate report. Circumferential tumour at "
                                                           "30 cm, biopsies taken."],
                                           summary=["I saw Mrs Ashworth today following her colonoscopy on 02/09/2024, which showed "
                                                    "a tumour in the sigmoid colon. Histology has confirmed adenocarcinoma. I explained "
                                                    "the findings with her husband present.",
                                                    "She reports a change in bowel habit since around February this year and tiredness. "
                                                    "We will proceed with staging and discuss at MDT."],
                                           facts=[Fact("letter", "Adenocarcinoma of the sigmoid colon (biopsy confirmed)",
                                                       "Adenocarcinoma of the sigmoid colon (biopsy confirmed)", "2024-09-12")],
                                           refs=[{"described_document": "Colonoscopy report", "date": "2024-09-02",
                                                  "quote": "Colonoscopy 02/09/2024: see separate report", "planted": "3"},
                                                 {"described_document": "Histology report (colonic biopsies)", "date": "2024-09-02",
                                                  "quote": "Histology has confirmed adenocarcinoma"}]),
                    None, "wv"))
    out.append(Item(histology_report("wv-histo-biopsy-2024", lab="Westvale Laboratory Medicine", specimen_no="WH24-41187",
                                     received_lab=date(2024, 9, 2), reported=date(2024, 9, 6),
                                     specimen="Sigmoid colon biopsies x4 (colonoscopy 02/09/2024)",
                                     macro="Four pale tan fragments 2-4 mm.",
                                     micro="Fragments of colonic mucosa infiltrated by moderately differentiated adenocarcinoma. "
                                           "MMR proteins retained.",
                                     diagnosis="Sigmoid colon biopsies: moderately differentiated adenocarcinoma.",
                                     pathologist="Dr E Novak, Consultant Histopathologist", snomed="T67700 M81403"), None, "wv"))
    out.append(Item(radiology_report("wv-ct-cap-2024", trust="Westvale University Hospitals", style="boxed",
                                     exam="CT Chest Abdomen Pelvis with contrast", requested=date(2024, 9, 12),
                                     performed=date(2024, 9, 16), reported=date(2024, 9, 17),
                                     clinical="Sigmoid adenocarcinoma, staging",
                                     findings="Irregular thickening of the sigmoid colon over 5 cm with fat stranding. Two "
                                              "mesocolic nodes up to 9 mm. Liver and lungs clear. No ascites.",
                                     conclusion="Sigmoid tumour, radiological T3 N1 M0.",
                                     radiologist="Dr R Osei, Consultant Radiologist", accession="WVR24-77310"), None, "wv"))
    out.append(Item(westvale_clinic_letter("wv-mdt-2024", specialty="Colorectal MDT", consultant="Mr D Achebe",
                                           seen=date(2024, 9, 19), typed=date(2024, 9, 20),
                                           diagnosis=["Sigmoid adenocarcinoma T3 N1 M0"], outcome="For anterior resection.",
                                           plan=["Surgery list 21/10/2024", "Oncology review after histology"],
                                           summary=["The case was discussed at the colorectal multidisciplinary meeting."]), None, "wv"))
    out.append(Item(westvale_clinic_letter("wv-oncology-2024", specialty="Oncology", consultant="Dr N Fairley",
                                           seen=date(2024, 11, 18), typed=date(2024, 11, 19),
                                           diagnosis=["pT3 N1b (2/18) sigmoid adenocarcinoma, R0 resection"],
                                           outcome="Adjuvant CAPOX for 3 months.",
                                           plan=["Cycle 1 on 25/11/2024", "Bloods before each cycle", "CEA 3-monthly"],
                                           summary=["I met Mrs Ashworth and her husband to discuss adjuvant chemotherapy. "
                                                    "She is recovering well from surgery.",
                                                    "We discussed the benefits and side effects of CAPOX and she wishes to proceed."]),
                    None, "wv"))
    out.append(Item(ed_letter("wv-ed-2022", trust="Westvale University Hospitals - Emergency Department", attended=date(2022, 7, 15),
                              arrival="19:42", discharged="22:10", complaint="Inversion injury left ankle", triage="4 (standard)",
                              diagnosis="Sprain of lateral ligament, left ankle", treatment="Analgesia, crutches, advice",
                              followup="GP if not improving in 2 weeks", clinician="ENP L Hart",
                              xray="XR L ankle: no fracture (Ottawa rules positive)"), "scan", "wv"))
    out.append(Item(westvale_path("wv-path-preop-2024", collected=date(2024, 10, 8), reported=date(2024, 10, 8),
                                  lab_no="WL24-998812", rows=[("Haemoglobin", "109", "g/L", "115 - 165", "L"),
                                                              ("CEA", "6.8", "ug/L", "< 5.0", "H"),
                                                              ("Creatinine", "64", "umol/L", "45 - 84", "")],
                                  requester="Pre-op assessment"), None, "wv"))
    return out


def eastmere_items():
    out = []
    out.append(Item(eastmere_discharge("em-discharge-2019", admitted=date(2019, 3, 11), discharged=date(2019, 3, 14),
                                       ward="Ward 7 (Surgical)", consultant="Ms L Garner",
                                       complaint="RUQ pain, recurrent biliary colic.", diagnosis="Symptomatic gallstones",
                                       procedures=["Laparoscopic cholecystectomy 12/03/2019"],
                                       course="Uncomplicated procedure. Mobilising and eating on day 2. Wounds clean. "
                                              "Gallbladder sent for histology - report to follow.",
                                       meds=[("Paracetamol 500mg tablets", "1g four times a day when required", "NEW"),
                                             ("Amlodipine 5mg tablets", "5mg once daily", "UNCHANGED"),
                                             ("Levothyroxine 75 micrograms", "75mcg once daily", "UNCHANGED")],
                                       gp_actions=["Removal of clips day 10 at practice"],
                                       facts=[Fact("letter", "Symptomatic gallstones", "Symptomatic gallstones", "2019-03-14")]),
                    "poor", "em"))
    out[-1].pages[0].references_out.append({"described_document": "Gallbladder histology", "date": "2019-03-12",
                                            "quote": "Gallbladder sent for histology - report to follow."})
    out.append(Item(histology_report("em-histo-gb-2019", lab="Eastmere Pathology", specimen_no="EH19-2231",
                                     received_lab=date(2019, 3, 12), reported=date(2019, 3, 19), specimen="Gallbladder",
                                     macro="Gallbladder 75 mm containing multiple faceted stones.",
                                     micro="Chronic cholecystitis with Rokitansky-Aschoff sinuses. No dysplasia or malignancy.",
                                     diagnosis="Chronic cholecystitis and cholelithiasis. No malignancy.",
                                     pathologist="Dr G Price", snomed="T63000 M43000"), "scan", "em"))
    out.append(Item(kingsmead_letter("km-ortho-2021", dated=date(2021, 3, 26), consultant="Mr T Lindqvist",
                                     specialty="Orthopaedic Surgeon",
                                     body=["Thank you for asking me to see Margaret, who has had right knee pain for some months. "
                                           "Her X-ray shows mild medial compartment osteoarthritis.",
                                           "Examination shows a small effusion with good range of movement and a stable knee."],
                                     plan=["Physiotherapy (community)", "Weight management advice", "No surgical intervention needed"]),
                    "scan", "em"))
    out.append(Item(riverside_letter("rv-physio-2021", dated=date(2021, 6, 1), service="MSK Physiotherapy",
                                     clinician="J Adeyemi, Physiotherapist", sessions="5 of 6",
                                     summary="Quadriceps strengthening and graded activity for right knee OA.",
                                     outcome="Pain improved from 7/10 to 3/10. Discharged with home programme."), "scan", "em"))
    return out


# ============================================================ filler and appended pages
def filler(i: int) -> Item:
    """Routine one-page paperwork found in every bundle, in many styles."""
    r = random.Random(900 + i)
    kind = i % 14
    yr = r.randint(2004, 2023)
    if 2014 < yr < 2017:
        yr = 2013
    d = date(yr, r.randint(1, 12), r.randint(1, 12))      # day 1-12 keeps DMY ambiguity in play
    lab = f"{str(yr)[2:]}-{r.randint(100000, 999999)}"
    if kind == 0:
        return Item(lab_report(f"fill-lab-{i}", collected=d, reported=d + timedelta(days=1), requester="DR R PATEL",
                               lab_no=f"NP{lab}", lines=[("Sodium", str(r.randint(137, 143)), "mmol/L", "133 - 146", ""),
                                                         ("Potassium", f"{r.uniform(3.8, 4.9):.1f}", "mmol/L", "3.5 - 5.3", ""),
                                                         ("Creatinine", str(r.randint(58, 78)), "umol/L", "45 - 84", ""),
                                                         ("eGFR", ">90", "mL/min", ">90", "")]),
                    r.choice(["scan", "fax"]), "fill")
    if kind == 1:
        return Item(westvale_path(f"fill-wvpath-{i}", collected=d, reported=d, lab_no=f"WL{lab}",
                                  rows=[("Cholesterol", f"{r.uniform(4.4, 5.9):.1f}", "mmol/L", "< 5.0", ""),
                                        ("HbA1c", str(r.randint(34, 40)), "mmol/mol", "20 - 41", "")], requester="Dr R Patel"),
                    r.choice([None, "scan"]), "fill")
    if kind == 2:
        return Item(pharmacy_note(f"fill-pharm-{i}", dated=d, pharmacy=r.choice(["Mill Road Pharmacy", "Larch Pharmacy"]),
                                  reason=r.choice(["Minor eye infection", "Insect bite", "Hay fever", "Cold sore", "Earache"]),
                                  advice="Self care advice given.", supplied=r.choice(["Chloramphenicol drops", "Antihistamine", "None"])),
                    "scan", "fill")
    if kind == 3:
        return Item(northam_letter(f"fill-ng-{i}", dept=r.choice(["Outpatient Appointments", "Physiotherapy Department", "Audiology"]),
                                   author="Appointments Office", author_role="Northam General Hospital", dated=d,
                                   dictated=d, typed=d, received=None, title=r.choice(["Appointment confirmation", "Appointment rescheduled"]),
                                   paragraphs=[f"An appointment has been booked for {(d + timedelta(days=21)).strftime('%d/%m/%Y')}. "
                                               f"Please bring a list of your current medicines."]), r.choice(["scan", "poor"]), "fill")
    if kind == 4:
        return Item(nhs111_report(f"fill-111-{i}", call_date=d, time=f"{r.randint(18, 23)}:{r.randint(10, 59)}",
                                  symptoms=r.choice(["Cough", "Back pain", "Headache", "Urinary symptoms", "Toothache"]),
                                  disposition="Self care", advice="Self care advice and when to call back.", sent=d), None, "fill")
    if kind == 5:
        part = r.choice([("XR Chest", "Lungs clear. Heart size normal. No pleural effusion.", "Normal chest radiograph."),
                         ("XR Lumbar spine", "Mild degenerative change L4/5. Alignment normal.", "Mild lumbar spondylosis."),
                         ("US Pelvis", "Uterus and ovaries normal for age. Endometrium 3 mm.", "Normal pelvic ultrasound.")])
        return Item(radiology_report(f"fill-rad-{i}", trust=r.choice(["Northam General Hospital", "Westvale University Hospitals"]),
                                     style=r.choice(["plain", "boxed"]), exam=part[0], requested=d, performed=d + timedelta(days=9),
                                     reported=d + timedelta(days=10), clinical=r.choice(["Cough 6 weeks", "Back pain", "Pelvic discomfort"]),
                                     findings=part[1], conclusion=part[2], radiologist="Dr H Iqbal", accession=f"R{lab}"),
                    r.choice(["scan", None]), "fill")
    if kind == 6:
        return Item(ed_letter(f"fill-ed-{i}", trust="Northam General Hospital - Emergency Department", attended=d,
                              arrival=f"{r.randint(9, 22)}:{r.randint(10, 59)}", discharged=f"{r.randint(9, 23)}:{r.randint(10, 59)}",
                              complaint=r.choice(["Laceration to finger", "Wasp sting", "Minor head injury, no LOC", "Foreign body eye"]),
                              triage="4 (standard)", diagnosis=r.choice(["Simple laceration - steristrips", "Local reaction",
                                                                         "Minor head injury", "Corneal abrasion"]),
                              treatment="Treated and discharged", followup="None required", clinician="ENP R Ellis"),
                    "scan", "fill")
    if kind == 7:
        return Item(riverside_letter(f"fill-rv-{i}", dated=d, service=r.choice(["Podiatry", "MSK Physiotherapy", "Audiology"]),
                                     clinician=r.choice(["S Grey, Podiatrist", "J Adeyemi, Physiotherapist", "P Lowe, Audiologist"]),
                                     sessions=str(r.randint(1, 4)), summary=r.choice(["Corn right foot treated.", "Shoulder strain, exercises.",
                                                                                     "Hearing within normal limits."]),
                                     outcome="Discharged."), "scan", "fill")
    if kind == 8:
        return Item(screening_letter(f"fill-screen-{i}", programme=r.choice(["NHS Breast Screening Programme", "NHS Cervical Screening Programme"]),
                                     dated=d, result="Reminder: your screening appointment",
                                     body=["You are due for screening. Please book an appointment at a time that suits you."],
                                     sender="Screening office"), "scan", "fill")
    if kind == 9:
        return Item(histology_report(f"fill-histo-{i}", lab="Northam Pathology", specimen_no=f"NH{lab}", received_lab=d,
                                     reported=d + timedelta(days=6), specimen=r.choice(["Skin, left shoulder (shave)", "Skin, back (punch)"]),
                                     macro="Skin ellipse 6 x 4 mm.", micro="Seborrhoeic keratosis. No atypia.",
                                     diagnosis="Seborrhoeic keratosis, completely excised.", pathologist="Dr G Price",
                                     snomed="T02000 M72750"), "scan", "fill")
    if kind == 10:
        return Item(ecg(f"fill-ecg-{i}", recorded=d, time=f"{r.randint(8, 17)}:{r.randint(10, 59)}", rate=r.randint(62, 84),
                        pr=r.randint(140, 180), qrs=r.randint(80, 100), qt=r.randint(360, 400), qtc=r.randint(390, 430),
                        interp="Sinus rhythm. Normal ECG.", where="Brookvale Surgery"), "scan", "fill")
    if kind == 11:
        od = date(r.randint(1987, 2002), r.randint(1, 12), r.randint(1, 28))
        return Item(typewriter_letter(f"fill-old-{i}", org="Northam Health Authority", org_lines=[r.choice(["Outpatients", "Department of Surgery"]),
                                      "Hospital Road, Northam"], dated=od, to_lines=["Dr J Carr", "Hollins Lane Medical Centre", "Northam"],
                                      re_line=f"Margaret Ashworth - {r.choice(['ingrowing toenail', 'ganglion right wrist', 'recurrent tonsillitis'])}",
                                      body=["Seen in clinic today. Treated as discussed. Discharged back to your care."],
                                      sign=r.choice(["Mr P Dunn", "Dr S Bell"]), role="Consultant"), "poor", "fill")
    if kind == 12:
        return Item(kingsmead_letter(f"fill-km-{i}", dated=d, consultant="Mr T Lindqvist", specialty="Orthopaedic Surgeon",
                                     body=["Margaret attended for review of her knee. She is doing well with her exercises."],
                                     plan=["Continue home exercises", "No further follow up"]), "scan", "fill")
    return Item(westvale_path(f"fill-wvpath2-{i}", collected=d, reported=d, lab_no=f"WL{lab}",
                              rows=[("TSH", f"{r.uniform(0.9, 3.4):.1f}", "mU/L", "0.27 - 4.2", ""),
                                    ("Free T4", f"{r.uniform(13, 19):.1f}", "pmol/L", "12 - 22", "")], requester="Dr S Okafor"),
                "scan", "fill")


def appended_records():
    """15 pages sent after the report was served (slice A and G tests)."""
    items = [Item(westvale_clinic_letter("app-oncology-2025-03", specialty="Oncology", consultant="Dr N Fairley",
                                         seen=date(2025, 3, 3), typed=date(2025, 3, 4), diagnosis=["Sigmoid adenocarcinoma, post adjuvant CAPOX"],
                                         outcome="Completed 4 cycles. Surveillance.", plan=["CT 12 months", "CEA 3-monthly"],
                                         summary=["Mrs Ashworth has completed adjuvant chemotherapy with grade 1 neuropathy."]), None, "app"),
             Item(westvale_path("app-cea-2025", collected=date(2025, 6, 2), reported=date(2025, 6, 3), lab_no="WL25-551023",
                                rows=[("CEA", "9.4", "ug/L", "< 5.0", "H")], comment="Rising CEA - clinician informed.",
                                requester="Dr N Fairley"), None, "app", tag="relevant_new")]
    i = 0
    while sum(x.n for x in items) < 14:
        items.append(filler(500 + i))
        i += 1
    # A short letter reporting an imaging result, in its own house style: the eval's "page 52"
    # case for the silent empty pages figure (evals/README.md, slice A).
    items.append(Item(northam_letter(
        "app-mri-shoulder-2025", dept="Department of Orthopaedics", author="Mr H Osei",
        author_role="Consultant Orthopaedic Surgeon", dated=date(2025, 4, 2), dictated=date(2025, 4, 2),
        typed=date(2025, 4, 3), received=date(2025, 4, 9), title="MRI right shoulder result",
        paragraphs=["The MRI right shoulder arranged at her last appointment shows a full thickness "
                    "supraspinatus tear with mild subacromial impingement.",
                    "I have referred her for a surgical opinion and would be grateful if you could "
                    "continue the current analgesia in the meantime."],
        gp_actions=["Continue current analgesia", "Await surgical opinion"],
        facts=[Fact("letter", "MRI right shoulder result", "MRI right shoulder result", "2025-04-02",
                    {"specialty": "Orthopaedics"}),
               Fact("result", "Full thickness supraspinatus tear with mild subacromial impingement.",
                    "full thickness supraspinatus tear with mild subacromial impingement", "2025-04-02",
                    {"test": "MRI right shoulder"})]),
        None, "app", tag="imaging_letter"))
    return items


# ============================================================ rendering and assembly
RENDERED: dict[str, tuple[str | None, list[list[dict]]]] = {}   # path -> (text before scanning, words drawn per page)


def render(item: Item, tmp: str, seed: int) -> str:
    if item.pdf and item.pdf[0] == "image":
        p = os.path.join(tmp, f"{item.pages[0].doc_id}-{seed}.pdf")
        RENDERED[p] = (None, [layout.in_inches(item.pdf[1], *A4_INCHES)])
        images_to_pdf([degrade(item.pdf[1], random.Random(seed), item.quality or "scan")], p)
        return p
    p = item.doc.save(tmp)
    if item.quality:
        sp = p.replace(".pdf", f"-{item.quality}-{seed}.pdf")
        RENDERED[sp] = (p, scan_pdf(p, sp, seed=seed, quality=item.quality, notes=item.notes))
        return sp
    return p


class LayoutWriter:
    """The layout sidecar for one bundle file: the pre-scan text page and the drawn words of each page."""

    def __init__(self):
        self.source, self.rows = PdfWriter(), []

    def add(self, path: str, k: int, overlay_pdf: str | None = None):
        src, drawn = RENDERED.get(path, (None, []))
        if src is not None:
            page = PdfReader(src).pages[k]
            self.source.add_page(page)
        else:
            self.source.add_blank_page(width=W, height=H)
        if overlay_pdf is not None:
            self.merge(len(self.rows), overlay_pdf)
        self.rows.append({"page": len(self.rows) + 1, "words": drawn[k] if k < len(drawn) else []})

    def merge(self, index: int, overlay_pdf: str):
        self.source.pages[index].merge_page(PdfReader(overlay_pdf).pages[0])

    def write(self, out_dir: str, name: str):
        d = os.path.join(out_dir, "layout")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}-source.pdf"), "wb") as fh:
            self.source.write(fh)
        with open(os.path.join(d, f"{name}.json"), "w") as fh:
            json.dump(self.rows, fh, indent=0, ensure_ascii=False)


def page_of_fact(items, planted_id):
    pos = 0
    for it in items:
        for k, pg in enumerate(it.pages):
            if any(f.planted == planted_id for f in pg.facts):
                return it, k, pos + k + 1
        pos += it.n
    raise KeyError(planted_id)


def place_target(items, locate, target, fill_counter, insert_at=None):
    """Insert one-page fillers (at insert_at, default just before the target item) until the target lands on `target`."""
    it, k, page = locate(items)
    if page > target:
        raise SystemExit(f"target {target} already passed (at {page}); reduce earlier content")
    idx = items.index(insert_at) if insert_at is not None else items.index(it)
    r = random.Random(target)
    while page < target:
        items.insert(r.randint(1, idx), filler(fill_counter[0]))     # spread among earlier documents
        fill_counter[0] += 1
        idx += 1
        page += 1
    return page


def main(out_root):
    out_dir = os.path.join(out_root, "bundles", "synthetic-mrs-a")
    exp_dir = os.path.join(out_root, "expected")
    tmp = os.path.join(out_root, "_tmp")
    for p in (out_dir, exp_dir, tmp):
        os.makedirs(p, exist_ok=True)

    def as_items(tuples):
        out = []
        for obj, q, g in tuples:
            if isinstance(obj, tuple) and obj[0] == "image":
                out.append(Item(None, q, g, pages=[obj[2]], pdf=("image", obj[1]), born_digital=False))
            else:
                out.append(Item(obj, q, g))
        return out

    gp = gp_section()
    gpc = gp_correspondence()
    ferritin = gpc[-1]
    wv_all = westvale_items() + as_items(westvale_series())
    wv_old = [it for it in wv_all if any(k in it.pages[0].doc_id for k in ("2010", "2022"))]
    wv_2024 = [it for it in wv_all if it not in wv_old]
    hw = as_items(handwritten_series())
    for it in hw:
        if it.pages[0].doc_id.startswith(("em-wardnotes", "wv-clinicnotes")):
            it.unreadable = True
    maternity = as_items(maternity_series(date(1995, 7, 19), 800, "baby boy")) + \
        as_items(maternity_series(date(1999, 2, 3), 820, "baby girl"))

    # as received: older trust records and paper notes first, then the GP print, then the cancer episode
    pre = northam_items() + as_items(northam_series()) + eastmere_items() + as_items(private_and_community()) + \
        wv_old + lloyd_george_items() + maternity + [castle_green_item()] + gpc[:-1]
    late = wv_2024 + [it for it in hw if it.group == "wv"] + [it for it in hw if it.group == "em"]
    items = pre + gp

    counter = [0]
    # planted 7 at page 118 (the 15 May consultation inside the full summary)
    place_target(items, lambda its: page_of_fact(its, "7"), TARGETS["7"], counter, insert_at=gp[0])
    _, _, p118 = page_of_fact(items, "7")
    idx118 = next(i for i, it in enumerate(items) if any(f.planted == "7" for pg in it.pages for f in pg.facts))

    # duplicates: re-scanned copies of single-page documents, spread between page 118 and page 402
    candidates = [it for it in items if it.n == 1 and it.group in ("ng", "gpc", "wv", "em") and it.doc is not None
                  and it.tag is None]
    RNG.shuffle(candidates)
    dups = []
    for it in candidates[:N_DUPLICATES]:
        dup = Item(it.doc, RNG.choice(["scan", "poor"]), "dup", pages=[it.pages[0]], born_digital=False)
        dup.dup_of = it
        dups.append(dup)
    if len(dups) < N_DUPLICATES:
        raise SystemExit(f"only {len(dups)} duplicate candidates")

    target_before = TARGETS["11_print"] - 1
    middle = items[idx118 + 1:]
    head_n = sum(it.n for it in items[:idx118 + 1])
    mid_real = sum(it.n for it in middle) + len(dups)
    late_take = []
    for it in list(late):                       # pull cancer-episode documents forward, keeping ~30 pages for the end
        if sum(x.n for x in late) - it.n < 30:
            break
        if head_n + mid_real + it.n <= target_before - 20:
            late_take.append(it)
            late.remove(it)
            mid_real += it.n
    middle = middle + late_take
    pad = target_before - head_n - mid_real
    if pad < 0:
        raise SystemExit(f"middle too long by {-pad}")
    rng2 = random.Random(7)
    fill_mid = [filler(counter[0] + i) for i in range(pad)]
    counter[0] += pad
    for extra in dups + fill_mid:
        middle.insert(rng2.randint(1, len(middle)), extra)
    head = items[:idx118 + 1] + middle
    tail = late
    need = TOTAL_PAGES - sum(x.n for x in head) - 1 - sum(x.n for x in tail)
    if need < 0:
        raise SystemExit(f"too many pages by {-need}")
    extra = [filler(counter[0] + i) for i in range(need)]
    counter[0] += need
    tail_mix = tail + extra
    rng2.shuffle(tail_mix)
    items = head + [ferritin] + tail_mix
    assert sum(x.n for x in items) == TOTAL_PAGES, sum(x.n for x in items)

    # ---- part 2: pages 451-900, detailed records, appended so part-1 page numbers never move
    p2 = as_items(all_part2(GP_RESULTS))
    p2_dup_src = [it for it in items + p2 if it.n == 1 and it.doc is not None and it.group not in ("dup",)
                  and it.tag is None and it.dup_of is None]
    rng3 = random.Random(902)
    rng3.shuffle(p2_dup_src)
    p2_dups = []
    for it in p2_dup_src[:45]:
        dup = Item(it.doc, rng3.choice(["scan", "poor", "fax"]), "dup", pages=[it.pages[0]], born_digital=False)
        dup.dup_of = it
        p2_dups.append(dup)
    need2 = PART2_PAGES - sum(x.n for x in p2) - len(p2_dups)
    if need2 < 0:
        raise SystemExit(f"part 2 too long by {-need2}")
    p2_fill = [filler(5000 + i) for i in range(need2)]
    for extra in p2_dups + p2_fill:
        p2.insert(rng3.randint(0, len(p2)), extra)
    items = items + p2
    print(f"part 2: {sum(x.n for x in p2) - len(p2_dups) - need2} detailed pages, {len(p2_dups)} duplicates, {need2} routine")
    assert sum(x.n for x in items) == TOTAL_PAGES + PART2_PAGES

    # render and assemble
    writer, page_rows, pos = PdfWriter(), [], 0
    lay = LayoutWriter()
    for n_item, it in enumerate(items):
        path = render(it, tmp, seed=1000 + n_item)
        reader = PdfReader(path)
        for k, pg in enumerate(reader.pages):
            if it.overlay is not None:
                pg.merge_page(PdfReader(it.overlay.save(tmp)).pages[0])
            writer.add_page(pg)
            lay.add(path, k, it.overlay.save(tmp) if it.overlay is not None else None)
            pos += 1
            info = it.pages[k]
            page_rows.append({"page": pos, "doc_id": info.doc_id, "page_in_doc": info.page_in_doc,
                              "source_type": info.source_type, "group": it.group,
                              "text_source": "embedded" if it.born_digital else "ocr",
                              "quality": it.quality or "born_digital",
                              "expected_low_confidence": bool(it.unreadable) or any(
                                  (f.fields or {}).get("messy", 0) >= 0.75 for f in info.facts),
                              "struck_out": [n for n in info.notes if isinstance(n, dict)],
                              "duplicate_of_doc": it.dup_of.pages[0].doc_id if it.dup_of else None,
                              "facts": [asdict(f) for f in info.facts], "printed_dates": info.printed_dates,
                              "availability": info.availability, "references_out": info.references_out})

    # planted item 5: one page with a typed header strip over a scanned body
    hdr_target = next(r for r in page_rows if r["group"] == "gpc" and r["text_source"] == "ocr" and r["doc_id"].startswith("gpc-optician"))
    ov = docman_header_overlay("docman-strip", filed_by="Jo Moss", filed_on=date(2022, 9, 14), doc_type="Optician letter",
                               received=date(2022, 9, 13))
    writer.pages[hdr_target["page"] - 1].merge_page(PdfReader(ov.save(tmp)).pages[0])
    lay.merge(hdr_target["page"] - 1, ov.save(tmp))
    hdr_target["text_source"] = "ocr"
    hdr_target["typed_header_scanned_body"] = True

    # resolve duplicate page numbers
    first_page = {}
    for r in page_rows:
        if r["duplicate_of_doc"] is None:
            first_page.setdefault(r["doc_id"], r["page"])
    for r in page_rows:
        r["duplicate_of_page"] = first_page.get(r["duplicate_of_doc"]) if r["duplicate_of_doc"] else None

    rec_path = os.path.join(out_dir, "records.pdf")
    writer.add_metadata({"/Title": "Synthetic records - Mrs A (invented patient)", "/Author": "Avodeck evals generator"})
    with open(rec_path, "wb") as fh:
        writer.write(fh)

    lay.write(out_dir, "records")
    write_expected(exp_dir, page_rows)
    # appended records
    app_writer, app_rows, app_lay = PdfWriter(), [], LayoutWriter()
    for n_item, it in enumerate(appended_records()):
        path = render(it, tmp, seed=5000 + n_item)
        for k, pg in enumerate(PdfReader(path).pages):
            app_writer.add_page(pg)
            app_lay.add(path, k)
            info = it.pages[k]
            app_rows.append({"page": TOTAL_PAGES + PART2_PAGES + len(app_rows) + 1, "doc_id": info.doc_id, "facts": [asdict(f) for f in info.facts],
                             "relevant_new": it.tag == "relevant_new"})
    with open(os.path.join(out_dir, "appended-records.pdf"), "wb") as fh:
        app_writer.write(fh)
    app_lay.write(out_dir, "appended-records")
    json.dump(app_rows, open(os.path.join(exp_dir, "appended.json"), "w"), indent=1)
    shutil.rmtree(tmp)
    print(f"records.pdf: {pos} pages; appended: {len(app_rows)} pages; fillers used: {counter[0]}")


def write_expected(exp_dir, rows):
    j = lambda name, obj: json.dump(obj, open(os.path.join(exp_dir, name), "w"), indent=1, default=str)
    j("pages.json", rows)
    pack_src = {"gp_record": "primary_care_record", "referral": "primary_care_record", "hospital_letter": "hospital",
                "discharge_summary": "hospital", "hospital_notes": "hospital", "result": "results", "ooh": "out_of_hours",
                "letters": "letters", "admin": "letters"}
    j("sources.json", [{"page": r["page"], "source_type": pack_src.get(r["source_type"], "letters"),
                        "document_type": r["source_type"]} for r in rows])
    j("text_source.json", [{"page": r["page"], "text_source": r["text_source"],
                            "typed_header_scanned_body": r.get("typed_header_scanned_body", False)} for r in rows])
    facts = []
    for r in rows:
        if r["duplicate_of_page"]:
            continue
        for f in r["facts"]:
            facts.append({"page": r["page"], **f})
    j("facts.json", facts)
    j("duplicates.json", [{"page": r["page"], "copy_of_page": r["duplicate_of_page"]} for r in rows if r["duplicate_of_page"]])
    j("unreadable.json", [r["page"] for r in rows if r["expected_low_confidence"]])
    j("availability.json", [{"page": r["page"], **a} for r in rows for a in r["availability"]])
    refs = [{"page": r["page"], **x} for r in rows for x in r["references_out"]]
    j("references.json", refs)
    j("missing.json", [x for x in refs if x.get("planted") == "3"])
    j("struck_out.json", [{"page": r["page"], **so} for r in rows for so in r.get("struck_out", [])])
    planted = {}
    for r in rows:
        if r["duplicate_of_page"]:
            continue
        for f in r["facts"]:
            if f.get("planted"):
                planted.setdefault(f["planted"], []).append(r["page"])
    planted["5"] = [r["page"] for r in rows if r.get("typed_header_scanned_body")]
    planted["4"] = [r["page"] for r in rows if r["expected_low_confidence"]]
    planted["1"] = [r["page"] for r in rows if r["duplicate_of_page"]]
    j("planted.json", planted)
    j("gaps.json", [{"source": "gp_record", "series": "tasks", "last_entry_month": "2024-01",
                     "gap_from": "2024-02", "gap_to": "2024-12", "planted": "2",
                     "fact_kind": "task"}])
    j("findings.json", [
        {"check": "abnormal_result_no_action", "should_fire": True, "planted": "11", "evidence_pages": planted.get("11", [])},
        {"check": "referral_acknowledged", "should_fire": True, "planted": "12", "evidence_pages": planted.get("12", [])},
        {"check": "ooh_reached_clinician", "should_fire": False, "planted": "13", "evidence_pages": planted.get("13", [])},
    ])
    j("case.json", {"case_names": CASE_NAMES, "nhs_number": PATIENT["nhs"], "dob": PATIENT["dob"],
                    "postcode": PATIENT["address"][-1], "period_start": "2024-02-01", "period_end": "2024-09-30"})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
