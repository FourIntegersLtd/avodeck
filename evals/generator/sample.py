"""Render one example of every record format, for a clinician to check.

Usage: python evals/generator/sample.py OUT_DIR
"""
from __future__ import annotations

import os
import sys
from datetime import date

from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import mm

sys.path.insert(0, os.path.dirname(__file__))

from common import Doc, W, H, Fact  # noqa: E402
from gp_prints import EmisPrint, SystmOnePrint  # noqa: E402
from hospital import (northam_letter, westvale_clinic_letter, eastmere_discharge, lab_report,  # noqa: E402
                      ooh_report, suspected_cancer_form)
from scan import scan_pdf, lloyd_george_card, degrade, images_to_pdf  # noqa: E402
from story import PATIENT, CORE_CONSULTS, CORE_RESULTS  # noqa: E402
import random  # noqa: E402

PRINTED = date(2025, 10, 7)


def emis_brief():
    e = EmisPrint("01-emis-brief-summary", "Brief Summary", PRINTED, "Medical Records (J Moss)")
    e.patient_details(date(2003, 9, 1))
    e.problems(active=[(date(2012, 6, 18), "Essential hypertension", date(2024, 1, 22)),
                       (date(2017, 11, 2), "Hypothyroidism", date(2024, 1, 22)),
                       (date(2024, 5, 15), "Iron deficiency anaemia", date(2024, 5, 20)),
                       (date(2024, 8, 20), "Rectal bleeding", date(2024, 8, 20))],
               sig_past=[(date(2019, 3, 11), "Laparoscopic cholecystectomy", date(2019, 3, 14))],
               minor_past=None)
    e.medication([
        {"drug": "Amlodipine 5mg tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 8, 2), "type": "Repeat"},
        {"drug": "Levothyroxine sodium 75microgram tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 8, 2), "type": "Repeat"},
        {"drug": "Ferrous fumarate 210mg tablets", "dose": "One tablet three times a day", "qty": "84 tablet", "last": date(2024, 7, 30), "type": "Repeat"},
    ])
    e.allergies(["No known drug allergy   (recorded 01-Sep-2003)"])
    e.values([(date(2024, 8, 20), "Body weight", "61.8 kg"), (date(2024, 2, 12), "Blood pressure", "138/84 mmHg"),
              (date(2023, 11, 6), "Smoking status", "Never smoked tobacco"), (date(2023, 11, 6), "Alcohol intake", "4 units/week")])
    return e.doc


def emis_full():
    e = EmisPrint("02-emis-full-summary", "Full Summary", PRINTED, "Medical Records (J Moss)")
    e.patient_details(date(2003, 9, 1))
    e.problems(active=[(date(2012, 6, 18), "Essential hypertension", date(2024, 1, 22)),
                       (date(2017, 11, 2), "Hypothyroidism", date(2024, 1, 22)),
                       (date(2024, 5, 15), "Iron deficiency anaemia", date(2024, 5, 20))],
               sig_past=[(date(2019, 3, 11), "Laparoscopic cholecystectomy", date(2019, 3, 14))],
               minor_past=[(date(2021, 2, 8), "Knee pain", date(2021, 4, 1)),
                           (date(2016, 7, 4), "Acute sinusitis", date(2016, 7, 18))])
    e.medication([
        {"drug": "Amlodipine 5mg tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 8, 2), "type": "Repeat"},
        {"drug": "Levothyroxine sodium 75microgram tablets", "dose": "One tablet once a day", "qty": "28 tablet", "last": date(2024, 8, 2), "type": "Repeat"},
    ], past=[
        {"drug": "Loperamide 2mg capsules", "dose": "As directed", "qty": "30 capsule", "last": date(2024, 3, 4), "type": "Acute"},
    ])
    e.allergies(["No known drug allergy   (recorded 01-Sep-2003)"])
    e.consultations_heading()
    items = [("c", c.d, c) for c in CORE_CONSULTS] + [("r", r.reported, r) for r in CORE_RESULTS]
    items.sort(key=lambda t: t[1], reverse=True)   # newest first, as EMIS prints
    for kind, _, obj in items:
        if kind == "c":
            e.consultation(obj)
        else:
            e.result(obj)
    return e.doc


def systmone():
    s = SystmOnePrint("03-systmone-record", PRINTED, "Records Team")
    items = [("c", c.d, c) for c in CORE_CONSULTS] + [("r", r.reported, r) for r in CORE_RESULTS]
    items.sort(key=lambda t: t[1])
    for kind, _, obj in items:
        (s.consultation if kind == "c" else s.result)(obj)
    return s.doc


def all_docs():
    docs = []
    docs.append(("EMIS-style Brief summary (born-digital PDF)", emis_brief(), None))
    docs.append(("EMIS-style Full summary (born-digital PDF, newest first)", emis_full(), None))
    docs.append(("SystmOne-style subject access print (journal in date order)", systmone(), None))
    docs.append(("Trust A - Northam General: classic typed letter, received stamp (scanned)",
                 northam_letter("04-northam-haem-advice", dept="Department of Haematology",
                                author="Dr J Whitfield", author_role="Consultant Haematologist",
                                dated=date(2024, 5, 10), dictated=date(2024, 5, 8), typed=date(2024, 5, 10),
                                received=date(2024, 5, 22), title="Advice and Guidance response",
                                paragraphs=["Thank you for your Advice and Guidance request regarding this 55 year old "
                                            "lady with a microcytic anaemia (Hb 112 g/L, MCV 77 fL) and ferritin of 9 ug/L "
                                            "in February.",
                                            "Iron deficiency anaemia in a post-menopausal woman should be regarded as "
                                            "gastrointestinal in origin until proven otherwise. I would recommend a FIT "
                                            "test and referral on the suspected lower GI cancer pathway if positive, or "
                                            "upper and lower GI investigation in any case. Coeliac serology would also be "
                                            "reasonable.",
                                            "Oral iron replacement is appropriate in the meantime but should not delay "
                                            "investigation."],
                                gp_actions=["Arrange FIT and coeliac serology", "Refer for GI investigation"]), "scan"))
    docs.append(("Trust B - Westvale: modern clinic template with details table (born-digital)",
                 westvale_clinic_letter("05-westvale-colorectal", specialty="Colorectal Surgery",
                                        consultant="Mr D Achebe", seen=date(2024, 9, 12), typed=date(2024, 9, 13),
                                        diagnosis=["Adenocarcinoma of the sigmoid colon (biopsy confirmed)",
                                                   "Iron deficiency anaemia"],
                                        outcome="Staging CT arranged. Discussed at colorectal MDT 19/09/2024.",
                                        plan=["CT chest, abdomen and pelvis", "Pre-operative assessment",
                                              "Clinical nurse specialist contact given"],
                                        investigations=["Colonoscopy 02/09/2024: see separate report. Circumferential "
                                                        "tumour at 30 cm, biopsies taken."],
                                        summary=["I saw Mrs Ashworth today following her colonoscopy on 02/09/2024, "
                                                 "which showed a tumour in the sigmoid colon. Histology has confirmed "
                                                 "adenocarcinoma. I explained the findings with her husband present.",
                                                 "She reports a change in bowel habit since around February this year "
                                                 "and tiredness. We will proceed with staging and discuss at MDT."]), None))
    docs.append(("Trust C - Eastmere: electronic discharge summary grid (poor scan)",
                 eastmere_discharge("06-eastmere-discharge", admitted=date(2019, 3, 11), discharged=date(2019, 3, 14),
                                    ward="Ward 7 (Surgical)", consultant="Ms L Garner",
                                    complaint="RUQ pain, recurrent biliary colic.",
                                    diagnosis="Symptomatic gallstones",
                                    procedures=["Laparoscopic cholecystectomy 12/03/2019"],
                                    course="Uncomplicated procedure. Mobilising and eating on day 2. Wounds clean.",
                                    meds=[("Paracetamol 500mg tablets", "1g four times a day when required", "NEW"),
                                          ("Amlodipine 5mg tablets", "5mg once daily", "UNCHANGED"),
                                          ("Levothyroxine 75 micrograms", "75mcg once daily", "UNCHANGED")],
                                    gp_actions=["Removal of clips day 10 at practice"]), "poor"))
    docs.append(("Pathology report printed by the lab (fax quality); dates as 03/04/2024 = 3 April",
                 lab_report("07-lab-tft", collected=date(2024, 4, 1), reported=date(2024, 4, 3),
                            requester="DR R PATEL", lab_no="NP24-150022",
                            lines=[("TSH", "2.1", "mU/L", "0.27 - 4.2", ""), ("Free T4", "15.8", "pmol/L", "12 - 22", "")],
                            comment="Thyroid function within reference range."), "fax"))
    docs.append(("Out-of-hours case report (scanned)",
                 ooh_report("08-ooh-report", case_no="NUC-24-033187", received_at="14:22", call_date=date(2024, 3, 30),
                            clinician="Dr M Fenwick (OOH GP)",
                            presenting="Lower abdominal pain and loose stools for 4 weeks, worse today.",
                            notes="Obs: T 37.1, HR 84, BP 132/80. Abdo soft, mild lower tenderness, no guarding. "
                                  "No PR bleeding reported. Advised fluids, analgesia.",
                            outcome="Advised to see own GP next week for review and further investigation if not settling. "
                                    "Worsening advice given.",
                            sent_to_gp=date(2024, 3, 31), sent_time="08:02"), "scan"))
    docs.append(("Suspected cancer referral form (scanned)",
                 suspected_cancer_form("09-2ww-form", referred=date(2024, 8, 20), gp="Dr S Okafor",
                                       criteria_ticked=["Aged 40 and over with unexplained weight loss and abdominal pain",
                                                        "Aged 60 and over with iron-deficiency anaemia or change in bowel habit",
                                                        "Rectal bleeding with change in bowel habit"],
                                       all_criteria=["Aged 40 and over with unexplained weight loss and abdominal pain",
                                                     "Aged 50 and over with unexplained rectal bleeding",
                                                     "Aged 60 and over with iron-deficiency anaemia or change in bowel habit",
                                                     "Rectal bleeding with change in bowel habit",
                                                     "Abdominal or rectal mass",
                                                     "Positive FIT (faecal haemoglobin 10 ug Hb/g or more)"],
                                       history="55F. Tiredness since Feb 2024, microcytic anaemia (ferritin 9), loose stools, "
                                               "weight 64.2kg (May) to 61.8kg (Aug). Fresh red blood PR x2 this week. "
                                               "PR: no mass, blood on glove.",
                                       fit="Sent 20/08/2024 - result awaited"), "scan"))
    return docs


def cover(out_dir, docs):
    d = Doc("00-cover", "admin")
    d.text(d.ml, "Avodeck synthetic record formats - sample pack", "Sans-B", 16, lead=26)
    d.para(d.ml, "One example of each format that will make up the Mrs A test bundle. Everything is invented. "
                 "Please check each format against real prints you have seen and note what looks wrong: "
                 "headings, order, date styles, wording, what is missing.", W - d.ml - d.mr, "Sans", 10.5, lead=14)
    d.y -= 8
    for i, (label, _, q) in enumerate(docs, 1):
        d.para(d.ml, f"{i}.  {label}", W - d.ml - d.mr, "Sans", 10.5, lead=15)
    d.para(d.ml, f"{len(docs) + 1}.  Lloyd George continuation card, handwritten (scanned image)",
           W - d.ml - d.mr, "Sans", 10.5, lead=15)
    return d.save(out_dir)


def main(out_dir):
    parts = os.path.join(out_dir, "parts")
    os.makedirs(parts, exist_ok=True)
    docs = all_docs()
    paths = [cover(parts, docs)]
    for i, (label, doc, q) in enumerate(docs):
        p = doc.save(parts)
        if q:
            sp = p.replace(".pdf", "-scanned.pdf")
            scan_pdf(p, sp, seed=100 + i, quality=q)
            p = sp
        paths.append(p)
    lg = lloyd_george_card([
        (date(1998, 4, 3), ["c/o sore throat 3/7. T 37.8", "tonsils inflamed, no exudate", "Pen V 250 qds 10/7"]),
        (date(1999, 11, 22), ["BP check 142/88", "rpt 3/12. lifestyle advice"]),
        (date(2001, 6, 5), ["L knee pain after fall", "no effusion, full ROM", "analgesia, review prn"]),
    ], PATIENT, seed=7, messy=0.6)
    lgp = os.path.join(parts, "10-lloyd-george-card.pdf")
    images_to_pdf([degrade(lg, random.Random(9), "scan")], lgp)
    paths.append(lgp)
    w = PdfWriter()
    for p in paths:
        for pg in PdfReader(p).pages:
            w.add_page(pg)
    out = os.path.join(out_dir, "avodeck-record-formats-sample.pdf")
    with open(out, "wb") as f:
        w.write(f)
    print(out, sum(len(PdfReader(p).pages) for p in paths), "pages")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
