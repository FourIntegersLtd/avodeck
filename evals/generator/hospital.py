"""Hospital and other non-GP documents in deliberately different house styles.

NHS trusts all lay out letters differently, so the bundle uses several
invented trusts with distinct layouts. Extraction must work per page from
meaning, never from a fixed template.
"""
from __future__ import annotations

from datetime import date

from reportlab.lib.units import mm

from common import Doc, W, H, uk_date, long_date, fmt_nhs, stamp_received
from story import PATIENT, PRACTICE

P = PATIENT


def _addressee(d: Doc, gp: str, y: float, x: float | None = None, size=10, font="Serif"):
    x = x or d.ml
    for ln in [gp, PRACTICE["name"], *PRACTICE["address"]]:
        d.text(x, ln, font, size, y=y)
        y -= size * 1.25
    return y


# ------------------------------------------------------------ Trust A
def northam_letter(doc_id: str, *, dept: str, author: str, author_role: str, dated: date,
                   dictated: date, typed: date, received: date | None, title: str,
                   paragraphs: list[str], diagnosis: list[str] | None = None,
                   gp_actions: list[str] | None = None, facts=None, refs=None) -> Doc:
    """Northam General Hospital NHS Trust: classic typed letter, serif, address block right."""
    d = Doc(doc_id, "hospital_letter", margin_l=22 * mm, margin_r=22 * mm, margin_t=18 * mm)
    c = d.c
    y = H - 18 * mm
    c.setFont("Serif-B", 13)
    c.drawRightString(W - d.mr, y, "Northam General Hospital")
    c.setFont("Serif", 9)
    for ln in ["NHS Trust", dept, "Hospital Road, Northam NM1 3HG", "Tel 01632 960500"]:
        y -= 4.4 * mm
        c.drawRightString(W - d.mr, y, ln)
    y = H - 50 * mm
    c.setFont("Serif", 9)
    c.drawString(d.ml, H - 30 * mm, f"Our ref: {P['hospital_numbers']['northam']}/{dept[:4].upper()}")
    c.drawString(d.ml, H - 34.5 * mm, f"Clinic date: {uk_date(dated)}")
    _addressee(d, "Dr R Patel", y)
    d.y = y - 30 * mm
    d.text(d.ml, uk_date(dated), "Serif", 10, lead=18)
    d.printed_date(dated)
    d.text(d.ml, "Dear Dr Patel", "Serif", 10.5, lead=16)
    d.para(d.ml, f"Re: {P['title']} {P['first']} {P['last']}, DOB {uk_date(P['dob'])}, "
                 f"NHS No {fmt_nhs(P['nhs'])}, {', '.join(P['address'])}", W - d.ml - d.mr, "Serif-B", 10)
    d.y -= 3
    d.text(d.ml, title, "Serif-B", 10.5, lead=16)
    if diagnosis:
        d.text(d.ml, "Diagnosis:", "Serif-B", 10)
        for ln in diagnosis:
            d.text(d.ml + 6 * mm, ln, "Serif", 10)
        d.y -= 4
    for ptext in paragraphs:
        d.para(d.ml, ptext, W - d.ml - d.mr, "Serif", 10.5, lead=14)
        d.y -= 6
    if gp_actions:
        d.text(d.ml, "Actions for GP:", "Serif-B", 10)
        for a in gp_actions:
            d.para(d.ml + 6 * mm, f"- {a}", W - d.ml - d.mr - 6 * mm, "Serif", 10.5)
    d.need(40 * mm)
    d.y -= 8
    d.text(d.ml, "Yours sincerely", "Serif", 10.5, lead=30)
    d.text(d.ml, author, "Serif-B", 10.5)
    d.text(d.ml, author_role, "Serif", 10, lead=20)
    d.text(d.ml, f"Dictated: {uk_date(dictated)}    Typed: {uk_date(typed)}", "Serif", 8.5)
    d.text(d.ml, "cc: Patient", "Serif", 8.5)
    if received:
        d.pages[0].notes.append("received stamp")
        # stamp on first page
        if len(d.pages) == 1:
            stamp_received(d, received)
        else:
            d.pages[0].availability.append({"kind": "received", "date": received.isoformat(),
                                            "quote": f"RECEIVED {received.strftime('%d %b %Y').upper()}"})
    for f in facts or []:
        d.pages[0].facts.append(f)
    for r in refs or []:
        d.pages[0].references_out.append(r)
    return d


# ------------------------------------------------------------ Trust B
def westvale_clinic_letter(doc_id: str, *, specialty: str, consultant: str, seen: date, typed: date,
                           diagnosis: list[str], outcome: str, plan: list[str], summary: list[str],
                           investigations: list[str] | None = None, facts=None, refs=None) -> Doc:
    """Westvale University Hospitals NHS Foundation Trust: modern template, banded header,
    patient details table, headed sections."""
    d = Doc(doc_id, "hospital_letter", margin_l=16 * mm, margin_r=16 * mm, margin_t=14 * mm)
    c = d.c
    band_h = 18 * mm
    d.box(0, H - band_h, W, band_h, fill=0.22, stroke=0.22)
    c.setFillGray(1)
    c.setFont("Sans-B", 13)
    c.drawString(d.ml, H - 10 * mm, "Westvale University Hospitals")
    c.setFont("Sans", 8.5)
    c.drawString(d.ml, H - 14.5 * mm, "NHS Foundation Trust  |  " + specialty)
    c.drawRightString(W - d.mr, H - 10 * mm, "Westvale Hospital, Park Lane, Westvale WV2 8LS")
    c.drawRightString(W - d.mr, H - 14.5 * mm, "Appointments 01632 960711")
    c.setFillGray(0)
    y = H - band_h - 8 * mm
    rows = [("Patient", f"{P['first']} {P['last'].upper()}"), ("Hospital no.", P["hospital_numbers"]["westvale"]),
            ("NHS no.", fmt_nhs(P["nhs"])), ("Date of birth", uk_date(P["dob"])),
            ("Clinic", f"{specialty} - {consultant}"), ("Seen", uk_date(seen))]
    tw = 92 * mm
    d.box(W - d.mr - tw, y - 6 * 4.8 * mm + 2 * mm, tw, 6 * 4.8 * mm + 2 * mm, stroke=0.5)
    yy = y - 2 * mm
    for k, v in rows:
        c.setFont("Sans-B", 8)
        c.drawString(W - d.mr - tw + 2 * mm, yy, k)
        c.setFont("Sans", 8)
        c.drawString(W - d.mr - tw + 26 * mm, yy, v)
        yy -= 4.8 * mm
    _addressee(d, "Dr R Patel", y - 2 * mm, size=9, font="Sans")
    d.y = y - 36 * mm
    d.printed_date(seen)
    d.text(d.ml, f"Typed {uk_date(typed)}", "Sans", 8, lead=14)

    def head(t):
        d.need(14 * mm)
        d.y -= 3
        d.text(d.ml, t.upper(), "Sans-B", 9, color=(0.15, 0.3, 0.35), lead=13)

    head("Diagnosis")
    for i, ln in enumerate(diagnosis, 1):
        d.para(d.ml + 4 * mm, f"{i}. {ln}", W - d.ml - d.mr - 4 * mm, "Sans", 9.5)
    head("Outcome")
    d.para(d.ml + 4 * mm, outcome, W - d.ml - d.mr - 4 * mm, "Sans", 9.5)
    head("Plan")
    for ln in plan:
        d.para(d.ml + 4 * mm, f"• {ln}", W - d.ml - d.mr - 4 * mm, "Sans", 9.5)
    if investigations:
        head("Investigations")
        for ln in investigations:
            d.para(d.ml + 4 * mm, ln, W - d.ml - d.mr - 4 * mm, "Sans", 9.5)
    head("Clinical summary")
    d.text(d.ml, "Dear Dr Patel,", "Sans", 9.5, lead=14)
    for ptext in summary:
        d.para(d.ml, ptext, W - d.ml - d.mr, "Sans", 9.5, lead=12.5)
        d.y -= 5
    d.need(26 * mm)
    d.y -= 8
    d.text(d.ml, "Kind regards,", "Sans", 9.5, lead=22)
    d.text(d.ml, consultant, "Sans-B", 9.5)
    d.text(d.ml, f"Consultant, {specialty}", "Sans", 9)
    d.text(d.ml, "Copy: patient (by post)", "Sans", 8)
    for f in facts or []:
        d.pages[0].facts.append(f)
    for r in refs or []:
        d.pages[0].references_out.append(r)
    return d


# ------------------------------------------------------------ Trust C (discharge)
def eastmere_discharge(doc_id: str, *, admitted: date, discharged: date, ward: str, consultant: str,
                       complaint: str, diagnosis: str, procedures: list[str], course: str,
                       meds: list[tuple[str, str, str]], gp_actions: list[str], facts=None) -> Doc:
    """Eastmere Hospitals NHS Trust: electronic discharge summary, boxed grid."""
    d = Doc(doc_id, "discharge_summary", margin_l=14 * mm, margin_r=14 * mm, margin_t=12 * mm)
    c = d.c
    c.setFont("Cond-B", 12)
    c.drawString(d.ml, H - 14 * mm, "INPATIENT DISCHARGE SUMMARY")
    c.setFont("Cond", 8)
    c.drawRightString(W - d.mr, H - 12 * mm, "Eastmere Hospitals NHS Trust")
    c.drawRightString(W - d.mr, H - 16 * mm, "Electronic discharge notification")
    d.y = H - 22 * mm
    full_w = W - d.ml - d.mr

    def grid(cells, h=9 * mm):
        n = len(cells)
        cw = full_w / n
        d.need(h + 2)
        for i, (k, v) in enumerate(cells):
            x = d.ml + i * cw
            d.box(x, d.y - h, cw, h, stroke=0.35)
            d.text(x + 1.5 * mm, k, "Cond-B", 7, y=d.y - 3.4 * mm)
            d.text(x + 1.5 * mm, v, "Cond", 8.5, y=d.y - 7.2 * mm)
        d.y -= h

    grid([("Patient", f"{P['last'].upper()}, {P['first']}"), ("DOB", uk_date(P["dob"])),
          ("NHS number", fmt_nhs(P["nhs"])), ("GP", f"Dr R Patel, {PRACTICE['name']}")])
    grid([("Admitted", uk_date(admitted)), ("Discharged", uk_date(discharged)), ("Ward", ward),
          ("Consultant", consultant)])
    d.printed_date(admitted)
    d.printed_date(discharged)

    def block(title, lines):
        d.y -= 2
        d.need(14 * mm)
        d.box(d.ml, d.y - 5 * mm, full_w, 5 * mm, fill=0.85, stroke=0.35)
        d.text(d.ml + 1.5 * mm, title, "Cond-B", 8, y=d.y - 3.6 * mm)
        d.y -= 9 * mm
        for ln in lines:
            d.para(d.ml + 2 * mm, ln, full_w - 4 * mm, "Cond", 8.8)
        d.y -= 1

    block("PRESENTING COMPLAINT", [complaint])
    block("PRIMARY DIAGNOSIS", [diagnosis])
    block("PROCEDURES", procedures or ["None"])
    block("CLINICAL COURSE", [course])
    d.y -= 2
    d.need(30 * mm)
    d.box(d.ml, d.y - 5 * mm, full_w, 5 * mm, fill=0.85, stroke=0.35)
    d.text(d.ml + 1.5 * mm, "MEDICATION ON DISCHARGE", "Cond-B", 8, y=d.y - 3.6 * mm)
    d.y -= 9 * mm
    for name, dose, status in meds:
        d.text(d.ml + 2 * mm, name, "Cond", 8.8, advance=False)
        d.text(d.ml + 80 * mm, dose, "Cond", 8.8, advance=False)
        d.text(d.ml + 150 * mm, status, "Cond-B", 8.8)
    block("ACTIONS FOR GP", gp_actions or ["None"])
    for f in facts or []:
        d.pages[0].facts.append(f)
    return d


# ------------------------------------------------------------ Pathology report
def lab_report(doc_id: str, *, collected: date, reported: date, requester: str, lab_no: str,
               lines: list[tuple[str, str, str, str, str]], comment: str | None = None, facts=None) -> Doc:
    """Northam Pathology Service printed report (often scanned into the GP record)."""
    d = Doc(doc_id, "result", margin_l=16 * mm, margin_r=16 * mm, margin_t=14 * mm)
    c = d.c
    c.setFont("Mono-B", 11)
    c.drawString(d.ml, H - 16 * mm, "NORTHAM PATHOLOGY SERVICE")
    c.setFont("Mono", 8)
    c.drawString(d.ml, H - 20 * mm, "Blood Sciences  Hospital Road Northam NM1 3HG  Tel 01632 960540")
    d.y = H - 30 * mm
    info = [("PATIENT", f"{P['last'].upper()} {P['first'].upper()}"), ("DOB", uk_date(P["dob"])),
            ("NHS NO", fmt_nhs(P["nhs"])), ("LAB NO", lab_no), ("REQUESTED BY", requester),
            ("SOURCE", PRACTICE["name"].upper()), ("COLLECTED", uk_date(collected)), ("REPORTED", uk_date(reported))]
    for i in range(0, len(info), 2):
        (k1, v1), (k2, v2) = info[i], info[i + 1]
        d.text(d.ml, f"{k1:<13}{v1}", "Mono", 8.5, advance=False)
        d.text(d.ml + 95 * mm, f"{k2:<13}{v2}", "Mono", 8.5)
    d.printed_date(collected)
    d.printed_date(reported)
    d.y -= 4
    d.hline(gray=0.2)
    d.y -= 8
    d.text(d.ml, f"{'TEST':<28}{'RESULT':>10}  {'UNITS':<10}{'REF RANGE':<16}FLAG", "Mono-B", 8.5)
    d.y += 4
    d.hline(gray=0.2)
    d.y -= 10
    for test, val, unit, rng, flag in lines:
        d.text(d.ml, f"{test:<28}{val:>10}  {unit:<10}{rng:<16}{flag}", "Mono", 8.5)
    if comment:
        d.y -= 6
        d.para(d.ml, f"COMMENT: {comment}", W - d.ml - d.mr, "Mono", 8.5)
    d.y -= 8
    d.text(d.ml, "*** END OF REPORT ***", "Mono", 8)
    for f in facts or []:
        d.pages[0].facts.append(f)
    return d


# ------------------------------------------------------------ Out of hours
def ooh_report(doc_id: str, *, case_no: str, received_at: str, call_date: date, clinician: str,
               presenting: str, notes: str, outcome: str, sent_to_gp: date, sent_time: str, facts=None) -> Doc:
    """Northam Urgent Care (out-of-hours) patient case report."""
    d = Doc(doc_id, "ooh", margin_l=15 * mm, margin_r=15 * mm, margin_t=14 * mm)
    c = d.c
    c.setFont("Sans-B", 11)
    c.drawString(d.ml, H - 16 * mm, "Northam Urgent Care - Out of Hours Service")
    c.setFont("Sans", 9)
    c.drawString(d.ml, H - 21 * mm, "Patient Case Report (for registered GP)")
    c.drawRightString(W - d.mr, H - 16 * mm, f"Case No: {case_no}")
    d.y = H - 30 * mm
    rows = [("Patient", f"{P['first']} {P['last']}"), ("DOB", uk_date(P["dob"])), ("NHS No", fmt_nhs(P["nhs"])),
            ("Registered GP", f"Dr R Patel, {PRACTICE['name']}"), ("Call received", f"{uk_date(call_date)} {received_at}"),
            ("Case type", "Base visit (PCC)"), ("Clinician", clinician)]
    for k, v in rows:
        d.text(d.ml, k, "Sans-B", 9, advance=False)
        d.text(d.ml + 40 * mm, v, "Sans", 9)
    d.printed_date(call_date)
    d.y -= 4
    for title, body in [("Presenting Condition", presenting), ("Clinical Notes", notes), ("Outcome", outcome)]:
        d.need(18 * mm)
        d.box(d.ml, d.y - 1.8 * mm, W - d.ml - d.mr, 5.6 * mm, fill=0.9, stroke=0.9)
        d.text(d.ml + 1.5 * mm, title, "Sans-B", 9, lead=14)
        d.para(d.ml + 1.5 * mm, body, W - d.ml - d.mr - 3 * mm, "Sans", 9)
        d.y -= 4
    d.text(d.ml, f"Report sent electronically to registered GP: {uk_date(sent_to_gp)} {sent_time}", "Sans-I", 8.5)
    d.page.availability.append({"kind": "sent_to_gp", "date": sent_to_gp.isoformat(),
                                "quote": f"sent electronically to registered GP: {uk_date(sent_to_gp)}"})
    for f in facts or []:
        d.pages[0].facts.append(f)
    return d


# ------------------------------------------------------------ 2WW referral form
def suspected_cancer_form(doc_id: str, *, referred: date, gp: str, criteria_ticked: list[str],
                          all_criteria: list[str], history: str, fit: str, facts=None) -> Doc:
    d = Doc(doc_id, "referral", margin_l=14 * mm, margin_r=14 * mm, margin_t=12 * mm)
    c = d.c
    full_w = W - d.ml - d.mr
    d.box(d.ml, H - 26 * mm, full_w, 14 * mm, fill=0.15, stroke=0.15)
    c.setFillGray(1)
    c.setFont("Sans-B", 12)
    c.drawString(d.ml + 3 * mm, H - 18 * mm, "SUSPECTED LOWER GASTROINTESTINAL CANCER REFERRAL")
    c.setFont("Sans", 8)
    c.drawString(d.ml + 3 * mm, H - 23 * mm, "Urgent suspected cancer pathway - Westvale University Hospitals - send via e-RS")
    c.setFillGray(0)
    d.y = H - 34 * mm
    for k, v in [("Patient name", f"{P['first']} {P['last']}"), ("DOB", uk_date(P["dob"])),
                 ("NHS number", fmt_nhs(P["nhs"])), ("Referring GP", gp), ("Practice", PRACTICE["name"]),
                 ("Date of referral", uk_date(referred))]:
        d.box(d.ml, d.y - 2.2 * mm, full_w, 6.4 * mm, stroke=0.5)
        d.text(d.ml + 2 * mm, k, "Sans-B", 8.5, advance=False)
        d.text(d.ml + 50 * mm, v, "Sans", 9, lead=6.4 * mm / 0.75 * 0.75 + 0.1)
        d.y -= 1.0
    d.printed_date(referred)
    d.y -= 6
    d.text(d.ml, "Referral criteria (tick all that apply)", "Sans-B", 9.5, lead=15)
    for crit in all_criteria:
        d.need(7 * mm)
        d.box(d.ml + 1 * mm, d.y - 1 * mm, 3.6 * mm, 3.6 * mm, stroke=0.2, lw=0.8)
        if crit in criteria_ticked:
            c.setLineWidth(1.4)
            c.line(d.ml + 1.6 * mm, d.y + 0.6 * mm, d.ml + 2.6 * mm, d.y - 0.6 * mm)
            c.line(d.ml + 2.6 * mm, d.y - 0.6 * mm, d.ml + 4.4 * mm, d.y + 2.6 * mm)
        d.para(d.ml + 7 * mm, crit, full_w - 8 * mm, "Sans", 8.8)
        d.y -= 1
    d.y -= 4
    d.text(d.ml, "FIT result", "Sans-B", 9.5, advance=False)
    d.text(d.ml + 50 * mm, fit, "Sans", 9.5, lead=15)
    d.text(d.ml, "Clinical history", "Sans-B", 9.5, lead=13)
    d.para(d.ml, history, full_w, "Sans", 9)
    for f in facts or []:
        d.pages[0].facts.append(f)
    return d
