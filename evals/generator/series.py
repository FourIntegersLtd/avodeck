"""Larger series of hospital documents across the invented trusts, plus inpatient charts.

Each function returns a list of build.Item-compatible tuples: (doc_or_image, quality, group).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.units import mm

from common import Doc, W, H, uk_date, fmt_nhs, Fact, PageInfo
from hospital import northam_letter, westvale_clinic_letter, eastmere_discharge, lab_report
from more_formats import radiology_report, histology_report, ed_letter, westvale_path, kingsmead_letter, riverside_letter
from scan import hand_text, pick_hand, INKS, DPI
from story import PATIENT

P = PATIENT
R = random.Random(77)
LIB = "/usr/share/fonts/truetype/liberation"


# ------------------------------------------------------------ inpatient charts (images, handwritten)
def obs_chart(day: date, seed: int):
    """A NEWS2-style observation chart: printed grid, handwritten values."""
    rng = random.Random(seed)
    w, h = int(8.27 * DPI), int(11.69 * DPI)
    img = Image.new("RGB", (w, h), (252, 252, 250))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 13)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 16)
    d.text((60, 50), "WESTVALE UNIVERSITY HOSPITALS - OBSERVATION CHART (ADULT)", font=fb, fill=0)
    d.text((60, 74), f"Name: {P['first']} {P['last'].upper()}   Hosp no: {P['hospital_numbers']['westvale']}   "
                     f"DOB: {uk_date(P['dob'])}   Ward: Ward 21 (Colorectal)", font=f, fill=0)
    d.text((60, 94), f"Date: {uk_date(day)}", font=f, fill=0)
    rows = ["Resp rate", "SpO2 %", "O2 L/min", "BP sys", "BP dia", "Pulse", "Consciousness", "Temp", "NEWS2", "Initials"]
    times = ["02:00", "06:00", "10:00", "14:00", "18:00", "22:00"]
    x0, y0, cw, rh = 60, 140, 150, 58
    for j, t in enumerate(times):
        d.text((x0 + 160 + j * cw + 40, y0 - 22), t, font=fb, fill=0)
    for i, r in enumerate(rows):
        y = y0 + i * rh
        d.rectangle([x0, y, x0 + 160, y + rh], outline=(120, 120, 120))
        d.text((x0 + 6, y + 20), r, font=f, fill=0)
        for j in range(len(times)):
            d.rectangle([x0 + 160 + j * cw, y, x0 + 160 + (j + 1) * cw, y + rh], outline=(170, 170, 170),
                        fill=(255, 235, 235) if r == "NEWS2" else None)
    hand = pick_hand(rng, 0.5)
    ink = rng.choice(INKS)
    vals_by_time = []
    for j in range(len(times)):
        v = [str(rng.randint(14, 20)), str(rng.randint(94, 99)), "RA", str(rng.randint(112, 138)), str(rng.randint(64, 82)),
             str(rng.randint(72, 96)), "A", f"{rng.uniform(36.6, 37.6):.1f}", str(rng.randint(0, 2)), rng.choice(["LH", "KT", "MO"])]
        vals_by_time.append(v)
        for i, val in enumerate(v):
            hand_text(img, (x0 + 160 + j * cw + 30, y0 + i * rh + 34), val, rng, size=28, messy=0.45, hand=hand, ink=ink)
    pi = PageInfo(f"wv-obs-{day.isoformat()}", 1, "hospital_notes")
    pi.printed_dates.append(day.isoformat())
    pi.facts.append(Fact("examination", f"NEWS2 {vals_by_time[3][8]}", f"NEWS2 {vals_by_time[3][8]}", day.isoformat(),
                         {"chart": "observations", "handwritten": True}))
    return img, pi


def drug_chart(start: date, seed: int):
    rng = random.Random(seed)
    w, h = int(8.27 * DPI), int(11.69 * DPI)
    img = Image.new("RGB", (w, h), (252, 252, 250))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 13)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 16)
    d.text((60, 50), "INPATIENT MEDICATION CHART - REGULAR MEDICINES", font=fb, fill=0)
    d.text((60, 74), f"{P['first']} {P['last'].upper()}  {P['hospital_numbers']['westvale']}  DOB {uk_date(P['dob'])}  "
                     f"Allergies: NKDA", font=f, fill=0)
    meds = [("Enoxaparin", "40mg", "SC", "18:00"), ("Paracetamol", "1g", "PO", "QDS"), ("Amlodipine", "5mg", "PO", "08:00"),
            ("Levothyroxine", "75mcg", "PO", "06:00"), ("Ondansetron", "4mg", "IV/PO", "PRN"), ("Oxycodone MR", "5mg", "PO", "BD")]
    hand = pick_hand(rng, 0.6)
    ink = rng.choice(INKS)
    y = 130
    pi = PageInfo(f"wv-drugchart-{start.isoformat()}", 1, "hospital_notes")
    for name, dose, route, freq in meds:
        d.rectangle([60, y, w - 60, y + 110], outline=(130, 130, 130))
        d.text((70, y + 6), "Drug", font=f, fill=(90, 90, 90))
        d.text((420, y + 6), "Dose / Route / Freq", font=f, fill=(90, 90, 90))
        hand_text(img, (80, y + 50), name, rng, size=32, messy=0.6, hand=hand, ink=ink)
        hand_text(img, (430, y + 50), f"{dose} {route} {freq}", rng, size=30, messy=0.6, hand=hand, ink=ink)
        for k in range(5):
            x = 760 + k * 80
            d.rectangle([x, y + 30, x + 70, y + 100], outline=(180, 180, 180))
            hand_text(img, (x + 10, y + 75), rng.choice(["LH", "KT", "MO", "/"]) if rng.random() < .85 else "",
                      rng, size=22, messy=0.5, hand=hand, ink=ink)
        pi.facts.append(Fact("prescription", name, name, start.isoformat(), {"setting": "inpatient", "dose": dose,
                                                                              "handwritten": True}))
        y += 125
    pi.printed_dates.append(start.isoformat())
    return img, pi


# ------------------------------------------------------------ Westvale series (2024 cancer pathway + ED)
STD_HISTORY = [
    "Past medical history: essential hypertension (2012); autoimmune hypothyroidism (1998); laparoscopic cholecystectomy "
    "(2019); mild right knee osteoarthritis (2021).",
    "Current medication: amlodipine 5 mg once daily; levothyroxine 75 micrograms once daily; ferrous fumarate 210 mg "
    "three times daily. No known drug allergies.",
    "Social history: lives with her husband; two adult children; works as an office manager; never smoked; alcohol "
    "around 4 units a week. Independent with all activities of daily living.",
    "Family history: father had a myocardial infarction aged 68. No family history of bowel cancer known.",
]


def westvale_series():
    out = []
    oth = lambda **kw: westvale_clinic_letter(**kw)
    out.append((oth(doc_id="wv-cns-2024-09", specialty="Colorectal Nurse Specialist", consultant="K Doyle (CNS)",
                    seen=date(2024, 9, 20), typed=date(2024, 9, 20), diagnosis=["Sigmoid adenocarcinoma"],
                    outcome="Holistic needs assessment completed.", plan=["Prehabilitation exercise programme", "Dietitian review"],
                    summary=["I met Margaret after her MDT discussion to explain the surgical plan and offer support."] + STD_HISTORY), None, "wv"))
    out.append((oth(doc_id="wv-preop-2024-10", specialty="Pre-operative Assessment", consultant="Dr P Lund (Anaesthetist)",
                    seen=date(2024, 10, 8), typed=date(2024, 10, 9), diagnosis=["For laparoscopic anterior resection",
                                                                                  "Hypertension, hypothyroidism"],
                    outcome="ASA 2. Fit for surgery.", plan=["Iron infusion arranged pre-op (Hb 109)", "Stop HRT 4 weeks pre-op"],
                    investigations=["Hb 109 g/L, CEA 6.8 ug/L, ECG sinus rhythm"],
                    summary=["Seen in pre-assessment clinic. Exercise tolerance two flights of stairs."] + STD_HISTORY +
                            ["Airway Mallampati 1. Chest clear. Heart sounds normal. ECG sinus rhythm 72."]), None, "wv"))
    op = Doc("wv-opnote-2024-10-21", "hospital_notes", margin_l=16 * mm, margin_r=16 * mm)
    op.text(op.ml, "OPERATION NOTE", "Sans-B", 13, lead=18)
    for k, v in [("Patient", f"{P['first']} {P['last']}  {P['hospital_numbers']['westvale']}"), ("Date", "21/10/2024"),
                 ("Surgeon", "Mr D Achebe"), ("Assistant", "Ms F Grant (ST6)"), ("Anaesthetist", "Dr P Lund"),
                 ("Procedure", "Laparoscopic high anterior resection"),
                 ("Findings", "Tumour sigmoid colon at 30 cm, no peritoneal disease, liver smooth"),
                 ("Procedure details", "Medial to lateral mobilisation. High tie IMA. Stapled colorectal anastomosis, "
                                       "leak test negative. No defunctioning stoma."),
                 ("Specimen", "Sigmoid colon to histology"), ("Post-op plan", "Enhanced recovery. VTE prophylaxis 28 days.")]:
        op.text(op.ml, k, "Sans-B", 9.5)
        op.para(op.ml + 4 * mm, v, W - op.ml - op.mr - 4 * mm, "Sans", 9.5)
        op.y -= 3
    op.fact("letter", "Laparoscopic high anterior resection", event_date=date(2024, 10, 21))
    op.printed_date(date(2024, 10, 21))
    out.append((op, "scan", "wv"))
    for i in range(5):
        day = date(2024, 10, 21) + timedelta(days=i)
        img, pi = obs_chart(day, 600 + i)
        out.append((("image", img, pi), "scan", "wv"))
    img, pi = drug_chart(date(2024, 10, 21), 650)
    out.append((("image", img, pi), "scan", "wv"))
    out.append((eastmere_discharge("wv-discharge-2024-10", admitted=date(2024, 10, 21), discharged=date(2024, 10, 26),
                                   ward="Ward 21 (Colorectal)", consultant="Mr D Achebe",
                                   complaint="Elective admission for anterior resection.",
                                   diagnosis="Sigmoid adenocarcinoma", procedures=["Laparoscopic high anterior resection 21/10/2024"],
                                   course="Uneventful recovery on enhanced recovery pathway. Bowels opened day 3. Discharged day 5. "
                                          "Final histology to follow; oncology referral made.",
                                   meds=[("Enoxaparin 40mg", "SC once daily for 28 days", "NEW"),
                                         ("Paracetamol 1g", "four times a day when required", "NEW"),
                                         ("Amlodipine 5mg", "once daily", "UNCHANGED"),
                                         ("Levothyroxine 75 micrograms", "once daily", "UNCHANGED")],
                                   gp_actions=["Please review wound at day 10", "Continue enoxaparin to complete 28 days"],
                                   facts=[Fact("letter", "Laparoscopic high anterior resection 21/10/2024",
                                               "Laparoscopic high anterior resection 21/10/2024", "2024-10-26")]), None, "wv"))
    out.append((histology_report("wv-histo-resection-2024", lab="Westvale Laboratory Medicine", specimen_no="WH24-49002",
                                 received_lab=date(2024, 10, 21), reported=date(2024, 11, 1), specimen="Sigmoid colon (anterior resection)",
                                 macro="Segment of colon 230 mm with annular tumour 45 mm.",
                                 micro="Moderately differentiated adenocarcinoma invading through muscularis propria into pericolic fat. "
                                       "2 of 18 lymph nodes involved. Margins clear. No EMVI.",
                                 diagnosis="pT3 N1b (2/18) adenocarcinoma, R0. TNM 8.", pathologist="Dr E Novak",
                                 snomed="T67700 M81403"), None, "wv"))
    for c, dd in enumerate([date(2024, 11, 25), date(2024, 12, 16), date(2025, 1, 6), date(2025, 1, 27)], 1):
        out.append((oth(doc_id=f"wv-chemo-c{c}", specialty="Oncology Day Unit", consultant="Dr N Fairley",
                        seen=dd, typed=dd + timedelta(days=1), diagnosis=["pT3 N1b sigmoid adenocarcinoma - adjuvant CAPOX"],
                        outcome=f"Cycle {c} of CAPOX given.",
                        plan=["Capecitabine 14 days", f"Next cycle {uk_date(dd + timedelta(days=21))}", "FBC and U&E before next cycle"],
                        investigations=[f"Neutrophils {R.uniform(1.6, 3.2):.1f}, platelets {R.randint(140, 260)}, bilirubin {R.randint(8, 16)}"],
                        summary=[R.choice(["Tolerating treatment with mild nausea and fatigue.",
                                           "Grade 1 peripheral neuropathy noted; dose unchanged.",
                                           "Mild hand-foot syndrome; emollients advised."])]), None, "wv"))
        out.append((westvale_path(f"wv-path-chemo-c{c}", collected=dd - timedelta(days=2), reported=dd - timedelta(days=2),
                                  lab_no=f"WL{str(dd.year)[2:]}-{R.randint(100000, 999999)}",
                                  rows=[("Haemoglobin", str(R.randint(104, 122)), "g/L", "115 - 165", "L"),
                                        ("Neutrophils", f"{R.uniform(1.6, 3.2):.1f}", "10*9/L", "2.0 - 7.5", ""),
                                        ("Platelets", str(R.randint(140, 260)), "10*9/L", "150 - 400", ""),
                                        ("Bilirubin", str(R.randint(8, 16)), "umol/L", "< 21", "")], requester="Oncology"),
                     R.choice([None, "scan"]), "wv"))
    out.append((radiology_report("wv-xr-ankle-2022", trust="Westvale University Hospitals", style="boxed", exam="XR Left ankle",
                                 requested=date(2022, 7, 15), performed=date(2022, 7, 15), reported=date(2022, 7, 16),
                                 clinical="Inversion injury, Ottawa positive", findings="No fracture. Soft tissue swelling laterally.",
                                 conclusion="No bony injury.", radiologist="Dr R Osei", accession="WVR22-31188"), "scan", "wv"))
    out.append((ed_letter("wv-ed-2010", trust="Westvale General Hospital - Accident and Emergency", attended=date(2010, 11, 6),
                          arrival="02:14", discharged="05:30", complaint="Palpitations after caffeine", triage="3 (urgent)",
                          diagnosis="Sinus tachycardia, resolved", treatment="Observation, ECG, bloods normal",
                          followup="GP if recurs", clinician="Dr T Vance (FY2)", xray="ECG: sinus rhythm 88"), "poor", "wv"))
    return out


# ------------------------------------------------------------ Northam series (pre-2024 care)
def northam_series():
    out = []
    specs = [
        (date(2016, 11, 14), "Department of ENT", "Mr G Holt", "Consultant ENT Surgeon", "ENT clinic",
         ["Recurrent sinusitis. Nasendoscopy shows mild mucosal oedema, no polyps.",
          "I have recommended saline douching and a nasal steroid for three months. Discharged."], None),
        (date(2017, 6, 5), "Department of ENT", "Mr G Holt", "Consultant ENT Surgeon", "ENT follow up",
         ["Symptoms much improved on treatment. No further follow up."], None),
        (date(2019, 11, 25), "Menopause Clinic", "Dr S Byrne", "Consultant Gynaecologist", "Menopause clinic",
         ["Vasomotor symptoms well controlled on transdermal oestradiol and micronised progesterone.",
          "No unscheduled bleeding. Continue and review annually with you."], None),
        (date(2020, 8, 10), "Department of Dermatology", "Dr L Chen", "Consultant Dermatologist", "Dermatology (2WW skin)",
         ["Pigmented lesion left upper arm. Dermoscopy shows a benign naevus pattern.",
          "I have reassured her and discharged her. No excision needed."], None),
        (date(2018, 6, 18), "Rapid Access Chest Pain Clinic", "Dr C Morgan", "Consultant Cardiologist", "CT coronary angiogram result",
         ["CT coronary angiogram: calcium score 0, no coronary artery disease. No cardiology follow up required."], None),
        (date(2013, 3, 4), "Department of Medicine", "Dr A Rowe", "Consultant Physician", "Hypertension clinic follow up",
         ["BP now 132/82 on amlodipine. Discharged to GP care."], None),
        (date(2009, 9, 21), "Department of Orthopaedics", "Mr K Patel", "Consultant Orthopaedic Surgeon", "Fracture clinic",
         ["Undisplaced fracture of the right fifth metatarsal after a fall. Walking boot for 6 weeks. Discharged."], None),
        (date(2024, 10, 14), "Department of Gastroenterology", "Dr F Rahman", "Consultant Gastroenterologist",
         "Gastroenterology clinic - appointment cancelled",
         ["Mrs Ashworth's routine appointment for 14/10/2024 has been cancelled as she is now under the care of the "
          "colorectal team at Westvale following an urgent referral."], None),
    ]
    for i, (d, dept, who, role, title, paras, dx) in enumerate(specs):
        hist = [h.replace("ferrous fumarate 210 mg three times daily", "no other regular medicines")
                for h in STD_HISTORY if d.year >= 2012 or not h.startswith("Past")]
        hist = [h for h in hist if not ("2019" in h and d.year < 2019)]
        out.append((northam_letter(f"ng-series-{i}-{d.isoformat()}", dept=dept, author=who, author_role=role, dated=d,
                                   dictated=d - timedelta(days=1), typed=d + timedelta(days=R.randint(2, 9)),
                                   received=d + timedelta(days=R.randint(8, 16)) if R.random() < 0.6 else None,
                                   title=title, paragraphs=paras[:1] + (hist[1:3] if d.year < 2019 else hist) + paras[1:], diagnosis=dx,
                                   facts=[Fact("letter", title, title, d.isoformat(), {"department": dept})]),
                    R.choice(["scan", "scan", "poor"]), "ng"))
    for yr in (2012, 2015, 2018, 2021):
        d = date(yr, R.randint(1, 12), R.randint(1, 12))
        out.append((lab_report(f"ng-lab-annual-{yr}", collected=d, reported=d + timedelta(days=1), requester="DR R PATEL",
                               lab_no=f"NP{str(yr)[2:]}-{R.randint(100000, 999999)}",
                               lines=[("Haemoglobin", str(R.randint(124, 138)), "g/L", "115 - 165", ""),
                                      ("Ferritin", str(R.randint(28, 70)), "ug/L", "13 - 150", ""),
                                      ("TSH", f"{R.uniform(0.9, 3.1):.1f}", "mU/L", "0.27 - 4.2", ""),
                                      ("Cholesterol", f"{R.uniform(4.6, 5.8):.1f}", "mmol/L", "< 5.0", "")]),
                    R.choice(["fax", "scan"]), "ng"))
    return out


def continuation_sheets(doc_id, trust, entries, seed, messy=0.55, source="hospital_notes"):
    """Handwritten notes on printed continuation sheets, with the corrections real notes have.
    entries: [(date, time, author, [lines])]. Returns a list of (img, PageInfo), one per sheet."""
    from hand import ward_entry, crossed_section, signature, write_line
    rng = random.Random(seed)
    w, h = int(8.27 * DPI), int(11.69 * DPI)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 13)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 15)
    sheets, img, pi, y = [], None, None, 0
    line_h = 46

    def new_sheet():
        nonlocal img, pi, y
        img = Image.new("RGB", (w, h), (252, 252, 250))
        d = ImageDraw.Draw(img)
        d.text((60, 40), f"{trust.upper()} - CLINICAL NOTES (CONTINUATION SHEET)", font=fb, fill=0)
        d.rectangle([w - 520, 30, w - 50, 140], outline=(80, 80, 80), width=2)
        for k, t in enumerate([f"Name: {P['first']} {P['last'].upper()}", f"Hosp no: {P['hospital_numbers']['westvale']}",
                               f"DOB: {uk_date(P['dob'])}", f"NHS: {fmt_nhs(P['nhs'])}"]):
            d.text((w - 505, 38 + k * 24), t, font=f, fill=0)       # printed addressograph label
        d.line([(60, 160), (w - 50, 160)], fill=(120, 120, 120), width=2)
        d.line([(200, 160), (200, h - 60)], fill=(200, 120, 120), width=1)
        d.text((70, 166), "Date / time", font=f, fill=(90, 90, 90))
        for yy in range(200, h - 60, line_h):
            d.line([(60, yy), (w - 50, yy)], fill=(215, 215, 215), width=1)
        pi = PageInfo(f"{doc_id}-{len(sheets) + 1}", 1, source)
        y = 200 + line_h
        sheets.append((img, pi))

    new_sheet()
    for when, time, author, lines in entries:
        need = (len(lines) + 4) * line_h
        if y + need > h - 80:
            new_sheet()
        doctor = any(k in author for k in ("Dr", "Mr", "Ms", "WR", "FY", "ST", "SHO", "Achebe", "Garner"))
        m = min(0.95, messy + (0.25 if doctor else 0.0) + rng.uniform(-0.05, 0.1))
        hand = pick_hand(rng, m)
        ink = rng.choice(INKS)
        initials = "".join(p[0] for p in author.replace("Dr ", "").split()[:2]).upper() or "XX"
        # occasionally a block written in the wrong notes, crossed out first
        if rng.random() < 0.12 and y + 4 * line_h < h - 200:
            y0 = y - line_h + 10
            for junk in rng.sample(["Bed 14 - Mr K", "Chest pain overnight, trop sent", "Plan: echo, cardiology r/v",
                                    "Ward round: walking well"], 2):
                write_line(img, (215, y), junk, rng, size=27, ink=ink, messy=m, hand=hand)
                y += line_h
            crossed_section(img, 210, y0, w - 80, y - line_h + 20, rng, ink, initials)
            pi.notes.append("crossed section: written in error")
        write_line(img, (70, y), f"{when.day}/{when.month}/{str(when.year)[2:]}", rng, size=26, ink=ink, messy=m, hand=hand,
                   max_x=195, marks=False)
        write_line(img, (70, y + line_h), time, rng, size=24, ink=ink, messy=m, hand=hand, max_x=195, marks=False)
        y, struck = ward_entry(img, 215, y, lines, rng, line_h, size=27, ink=ink, messy=m, hand=hand,
                               initials=initials, max_x=w - 80)
        signature(img, (w - 420, y), author, rng, size=26, ink=ink, messy=m, hand=hand)
        y += line_h
        pi.printed_dates.append(when.isoformat())
        if struck:
            pi.notes.append({"struck_out": struck, "entry_date": when.isoformat()})
        pi.facts.append(Fact("consultation", lines[0], lines[0], when.isoformat(), {"author": author, "handwritten": True,
                                                                                   "setting": "inpatient", "messy": round(m, 2)}))
    return sheets


def printed_form_handwritten(doc_id, title, fields, when: date, seed, source="hospital_notes"):
    """A printed form (consent, nursing assessment) filled in by hand."""
    rng = random.Random(seed)
    w, h = int(8.27 * DPI), int(11.69 * DPI)
    img = Image.new("RGB", (w, h), (252, 252, 250))
    d = ImageDraw.Draw(img)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 18)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 14)
    d.text((60, 50), title, font=fb, fill=0)
    d.text((60, 80), f"Patient: {P['first']} {P['last'].upper()}   DOB {uk_date(P['dob'])}   "
                     f"Hosp no {P['hospital_numbers']['westvale']}", font=f, fill=0)
    hand = pick_hand(rng, 0.5)
    ink = rng.choice(INKS)
    y = 140
    for label, value in fields:
        d.text((60, y), label, font=f, fill=(60, 60, 60))
        d.line([(60, y + 62), (w - 60, y + 62)], fill=(180, 180, 180))
        hand_text(img, (80, y + 50), value, rng, size=28, messy=0.5, hand=hand, ink=ink)
        y += 92
    pi = PageInfo(doc_id, 1, source)
    pi.printed_dates.append(when.isoformat())
    pi.facts.append(Fact("letter", title, title, when.isoformat(), {"form": True, "handwritten": True}))
    return img, pi


def handwritten_series():
    out = []
    ward = [
        (date(2024, 10, 21), "17:40", "F Grant ST6", ["Post-op review. Lap high AR today.", "Obs stable, NEWS 1. Pain controlled.",
                                                      "Plan: ERAS, mobilise, free fluids, VTE prophylaxis"]),
        (date(2024, 10, 22), "08:15", "Mr Achebe WR", ["WR: comfortable, passing flatus.", "Abdo soft, wounds clean.",
                                                       "Plan: light diet, remove catheter"]),
        (date(2024, 10, 23), "09:00", "Dr M Osei FY1", ["Day 2. Nausea overnight, settled with ondansetron.", "Hb 101.",
                                                        "Plan: cont ERAS, bloods tomorrow"]),
        (date(2024, 10, 24), "08:40", "Mr Achebe WR", ["Bowels opened. Eating well.", "CRP 64 falling.", "Plan: home tomorrow if well"]),
        (date(2024, 10, 25), "10:10", "Dr M Osei FY1", ["Mobilising independently. Pain 2/10 on oral analgesia.",
                                                        "Not quite ready - husband away until tomorrow"]),
        (date(2024, 10, 26), "09:30", "F Grant ST6", ["Fit for discharge. TTO done.", "Enoxaparin 28 days taught.",
                                                      "CNS to call. OPA oncology"]),
    ]
    for img, pi in continuation_sheets("wv-wardnotes-2024", "Westvale University Hospitals", ward, 700, messy=0.6):
        out.append((("image", img, pi), "scan", "wv"))
    clinic = [
        (date(2024, 9, 12), "11:20", "D Achebe", ["Colorectal clinic. Seen with husband.", "Colonoscopy: sigmoid tumour 30cm.",
                                                  "Histology: adenoCa. CT CAP requested.", "For MDT 19/9. CNS present."]),
        (date(2024, 9, 26), "14:00", "D Achebe", ["Results clinic. CT: T3 N1 M0.", "Discussed lap AR, risks incl leak 5%,",
                                                  "stoma possible. Consented. Pre-op 8/10."]),
    ]
    for img, pi in continuation_sheets("wv-clinicnotes-2024", "Westvale University Hospitals", clinic, 710, messy=0.75):
        out.append((("image", img, pi), "poor", "wv"))
    em = [
        (date(2019, 3, 11), "15:30", "Dr J Fox", ["Admitted electively for lap chole.", "PMH HTN, hypothyroid. NKDA.", "Consent checked."]),
        (date(2019, 3, 12), "18:00", "Ms Garner", ["Lap chole done. Uneventful.", "GB to histology."]),
        (date(2019, 3, 13), "08:30", "Dr J Fox", ["Day 1. Shoulder tip pain, mobilising.", "Eating, drinking."]),
        (date(2019, 3, 14), "09:15", "Ms Garner", ["Well. Home today.", "Clips day 10 GP."]),
    ]
    for img, pi in continuation_sheets("em-wardnotes-2019", "Eastmere Hospitals", em, 720, messy=0.85):
        out.append((("image", img, pi), "poor", "em"))
    img, pi = printed_form_handwritten("wv-consent-2024", "CONSENT FORM 1 - PATIENT AGREEMENT TO INVESTIGATION OR TREATMENT",
                                       [("Proposed procedure", "Laparoscopic anterior resection +/- stoma"),
                                        ("Intended benefits", "Remove bowel cancer"),
                                        ("Serious or frequent risks", "Bleeding, infection, leak (5%), stoma, VTE, conversion to open"),
                                        ("Any extra procedures", "Defunctioning ileostomy if needed"),
                                        ("Health professional", "D Achebe, Consultant Surgeon 26/9/24"),
                                        ("Patient signature", "M Ashworth 26/9/24")], date(2024, 9, 26), 730)
    out.append((("image", img, pi), "scan", "wv"))
    img, pi = printed_form_handwritten("wv-nursing-adm-2024", "NURSING ADMISSION ASSESSMENT",
                                       [("Reason for admission", "Elective lap anterior resection"),
                                        ("Next of kin", "Husband - Peter"),
                                        ("Mobility", "Independent"), ("Diet", "Normal, reduced appetite"),
                                        ("Skin (Waterlow)", "Score 8 - low risk"), ("Falls risk", "Low"),
                                        ("Medicines brought in", "Amlodipine, levothyroxine, ferrous fumarate"),
                                        ("Admitting nurse", "L Hart RN 21/10/24 07:10")], date(2024, 10, 21), 740)
    out.append((("image", img, pi), "scan", "wv"))
    return out


def antenatal_card(year_birth: date, seed: int):
    """Hand-held antenatal record visits table (printed grid, handwritten rows)."""
    rng = random.Random(seed)
    w, h = int(8.27 * DPI), int(11.69 * DPI)
    img = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(img)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 17)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 13)
    d.text((60, 50), "NORTHAM MATERNITY SERVICE - ANTENATAL VISITS", font=fb, fill=0)
    d.text((60, 78), f"{P['first']} ASHWORTH   DOB {uk_date(P['dob'])}   EDD {uk_date(year_birth)}", font=f, fill=0)
    cols = [("Date", 130), ("Gest", 80), ("BP", 120), ("Urine", 110), ("Fundus", 100), ("Pres", 90), ("FH", 80),
            ("Comments", 330), ("Sig", 100)]
    x, y0 = 60, 120
    for name, cw in cols:
        d.rectangle([x, y0, x + cw, y0 + 34], outline=(90, 90, 90), fill=(230, 230, 220))
        d.text((x + 6, y0 + 9), name, font=f, fill=0)
        x += cw
    hand = pick_hand(rng, 0.55)
    ink = rng.choice(INKS)
    pi = PageInfo(f"mat-anc-{year_birth.year}", 1, "hospital_notes")
    start = year_birth - timedelta(weeks=28)
    rows = []
    for k, wk in enumerate([12, 16, 20, 25, 28, 31, 34, 36, 38, 40, 41]):
        when = year_birth - timedelta(weeks=40 - wk) + timedelta(days=rng.randint(-2, 2))
        bp = f"{rng.randint(104, 128)}/{rng.randint(62, 80)}"
        fund = "-" if wk < 20 else str(wk + rng.randint(-1, 1))
        row = [f"{when.day}/{when.month}/{str(when.year)[2:]}", str(wk), bp, "NAD", fund,
               "-" if wk < 28 else rng.choice(["Ceph", "ceph", "Ceph 4/5"]), "-" if wk < 16 else "heard",
               rng.choice(["well", "feels well", "mild SPD", "c/o heartburn", "ok"]), rng.choice(["MW", "JT", "mw"])]
        rows.append(row)
        yy = y0 + 34 + (k + 1) * 64
        x = 60
        for (name, cw), val in zip(cols, row):
            d.rectangle([x, yy - 64, x + cw, yy], outline=(170, 170, 170))
            hand_text(img, (x + 8, yy - 18), val, rng, size=26, messy=0.55, hand=hand, ink=ink)
            x += cw
        pi.printed_dates.append(when.isoformat())
        pi.facts.append(Fact("examination", f"BP {bp}", bp, when.isoformat(), {"gestation_weeks": wk, "handwritten": True}))
    return img, pi


def partogram(delivered: date, seed: int):
    rng = random.Random(seed)
    w, h = int(11.69 * DPI), int(8.27 * DPI)       # landscape chart
    img = Image.new("RGB", (w, h), (252, 250, 245))
    d = ImageDraw.Draw(img)
    fb = ImageFont.truetype(f"{LIB}/LiberationSans-Bold.ttf", 17)
    f = ImageFont.truetype(f"{LIB}/LiberationSans-Regular.ttf", 12)
    d.text((60, 30), f"PARTOGRAM   {P['first']} ASHWORTH   {uk_date(delivered)}", font=fb, fill=0)
    gx, gy, gw, gh = 140, 90, w - 220, 420
    for i in range(25):
        d.line([(gx + i * gw / 24, gy), (gx + i * gw / 24, gy + gh)], fill=(200, 210, 230))
    for j in range(11):
        d.line([(gx, gy + j * gh / 10), (gx + gw, gy + j * gh / 10)], fill=(200, 210, 230))
        d.text((gx - 40, gy + gh - j * gh / 10 - 7), str(j), font=f, fill=0)
    d.text((gx - 120, gy + gh / 2), "Cervix cm", font=f, fill=0)
    d.line([(gx + 4 * gw / 24, gy + gh * 0.6), (gx + 10 * gw / 24, gy)], fill=(200, 60, 60), width=2)   # alert line
    pts, cm, t = [], 4, 4
    while cm < 10:
        pts.append((gx + t * gw / 24, gy + gh - cm * gh / 10))
        t += rng.uniform(1.5, 2.5)
        cm += rng.choice([1, 2, 2, 3])
    pts.append((gx + t * gw / 24, gy))
    ink = rng.choice(INKS)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        d.line([(x1, y1), (x2, y2)], fill=ink, width=3)
    for x, y in pts:
        hand_text(img, (int(x) - 8, int(y) + 8), "x", rng, size=28, messy=0.4, ink=ink)
    hand = pick_hand(rng, 0.6)
    for k, txt in enumerate(["SROM clear liquor", "Entonox", "FH 130-150 reactive", f"SVD {rng.randint(10, 23)}:{rng.randint(10, 59)}",
                             "Female 3.4kg" if delivered.year == 1999 else "Male 3.6kg", "EBL 300ml"]):
        hand_text(img, (gx + 30 + (k % 3) * 360, gy + gh + 70 + (k // 3) * 60), txt, rng, size=28, messy=0.6, hand=hand, ink=ink)
    img = img.rotate(90, expand=True).resize((int(8.27 * DPI), int(11.69 * DPI)))
    pi = PageInfo(f"mat-partogram-{delivered.year}", 1, "hospital_notes")
    pi.printed_dates.append(delivered.isoformat())
    pi.facts.append(Fact("letter", "Spontaneous vaginal delivery", "SVD", delivered.isoformat(), {"handwritten": True, "chart": "partogram"}))
    return img, pi


def maternity_series(delivered: date, seed: int, baby: str):
    from more_formats import typewriter_letter
    rng = random.Random(seed)
    out = []
    booking = delivered - timedelta(weeks=28)
    img, pi = printed_form_handwritten(f"mat-booking-{delivered.year}", "NORTHAM MATERNITY SERVICE - BOOKING HISTORY",
                                       [("LMP / EDD", f"EDD {uk_date(delivered)}"), ("Gravida / Para", "G1 P0" if delivered.year == 1995 else "G2 P1"),
                                        ("Medical history", "Nil significant" if delivered.year == 1995 else "Hypothyroid on thyroxine 50"),
                                        ("Blood group", "O Rh positive"), ("Booking BP", "112/70"), ("Booking Hb", "124"),
                                        ("Smoking", "Never"), ("Midwife", "M Walsh RM")], booking, seed + 1)
    out.append((("image", img, pi), "poor", "mat"))
    for k, wk in enumerate((12, 20)):
        when = delivered - timedelta(weeks=40 - wk)
        out.append((radiology_report(f"mat-scan-{delivered.year}-{wk}", trust="Northam General Hospital", style="plain",
                                     exam=f"Obstetric ultrasound ({'dating' if wk == 12 else 'anomaly'})", requested=booking,
                                     performed=when, reported=when, clinical=f"{wk} weeks",
                                     findings="Single live intrauterine pregnancy. " + ("CRL consistent with dates."
                                              if wk == 12 else "Normal fetal anatomy. Placenta posterior, not low."),
                                     conclusion="Normal scan.", radiologist="Sonographer A Pike", accession=f"NGO{delivered.year % 100}{wk}{rng.randint(100, 999)}"),
                    "poor", "mat"))
    img, pi = antenatal_card(delivered, seed + 2)
    out.append((("image", img, pi), "poor", "mat"))
    img, pi = partogram(delivered, seed + 3)
    out.append((("image", img, pi), "poor", "mat"))
    post = [(delivered, "14:00", "J Tate RM", ["Transferred to postnatal ward.", "Breastfeeding, uterus well contracted.",
                                                "Lochia normal. PU."]),
            (delivered + timedelta(days=1), "09:00", "Dr P Shaw SHO", ["Day 1 postnatal check.", "Well, BP 118/72.",
                                                                        "Perineum healing. Hb 112."]),
            (delivered + timedelta(days=2), "10:30", "J Tate RM", ["Home today. Community MW informed.", "Contraception discussed."])]
    for img, pi in continuation_sheets(f"mat-postnatal-{delivered.year}", "Northam General Hospital", post, seed + 4, messy=0.7):
        out.append((("image", img, pi), "poor", "mat"))
    out.append((typewriter_letter(f"mat-discharge-{delivered.year}", org="Northam General Hospital",
                                  org_lines=["Maternity Unit", "Hospital Road, Northam"], dated=delivered + timedelta(days=3),
                                  to_lines=["Dr J Carr", "Hollins Lane Medical Centre", "Northam"],
                                  re_line=f"Margaret Ashworth - delivery {delivered.strftime('%d.%m.%y')}",
                                  body=[f"This lady had a spontaneous vaginal delivery of a healthy {baby} on {uk_date(delivered)} "
                                        f"after an uncomplicated pregnancy. Estimated blood loss 300 ml.",
                                        "Mother and baby were discharged on day 2. Postnatal check with you at 6 weeks please."],
                                  sign="J Tate", role="Midwife"), "poor", "mat"))
    return out


def private_and_community():
    out = []
    out.append((kingsmead_letter("km-gynae-2019", dated=date(2019, 9, 2), consultant="Miss R Okoye", specialty="Gynaecologist",
                                 body=["Margaret came to discuss perimenopausal symptoms. Examination and transvaginal ultrasound were "
                                       "normal, endometrial thickness 4 mm.", "We discussed HRT in detail."],
                                 plan=["Transdermal oestradiol with micronised progesterone", "Review with GP in 3 months"]),
                "scan", "em"))
    out.append((riverside_letter("rv-dietitian-2024", dated=date(2024, 11, 12), service="Community Dietetics",
                                 clinician="H Moore, Dietitian", sessions="1",
                                 summary="Post-operative weight loss, appetite reduced during chemotherapy. Weight 59.4 kg.",
                                 outcome="Food-first advice and oral nutritional supplements twice daily. Review 4 weeks."), "scan", "em"))
    out.append((riverside_letter("rv-dn-2024", dated=date(2024, 11, 4), service="District Nursing",
                                 clinician="District Nursing Team North", sessions="3 visits",
                                 summary="Wound review post anterior resection; port sites healed.",
                                 outcome="Wound healed. Discharged from district nursing caseload."), "scan", "em"))
    return out
