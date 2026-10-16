"""More document types, each in its own house style, so the bundle has real variety."""
from __future__ import annotations

import math
import random
from datetime import date

from reportlab.lib.units import mm

from common import Doc, W, H, uk_date, long_date, fmt_nhs, stamp_received, Fact
from story import PATIENT, PRACTICE

P = PATIENT


def _f(d: Doc, kind, value, quote=None, when=None, **kw):
    d.fact(kind, value, quote=quote, event_date=when, **kw)


# ------------------------------------------------------------ 1990s typewritten letter
def typewriter_letter(doc_id, *, org, org_lines, dated: date, to_lines, re_line, body, sign, role, facts=()):
    d = Doc(doc_id, "hospital_letter", margin_l=24 * mm, margin_r=24 * mm)
    d.c.setFont("Mono-B", 11)
    d.c.drawCentredString(W / 2, H - 20 * mm, org.upper())
    d.c.setFont("Mono", 8.5)
    for i, ln in enumerate(org_lines):
        d.c.drawCentredString(W / 2, H - 25 * mm - i * 4 * mm, ln)
    d.y = H - 48 * mm
    for ln in to_lines:
        d.text(d.ml, ln, "Mono", 9.5)
    d.y -= 8
    d.text(d.ml, dated.strftime("%d %B %Y").lstrip("0"), "Mono", 9.5, lead=18)
    d.printed_date(dated)
    d.text(d.ml, "Dear Doctor,", "Mono", 9.5, lead=16)
    d.para(d.ml, re_line.upper(), W - d.ml - d.mr, "Mono-B", 9.5)
    d.y -= 6
    for para in body:
        d.para(d.ml, para, W - d.ml - d.mr, "Mono", 9.5, lead=13)
        d.y -= 7
    d.y -= 6
    d.text(d.ml, "Yours sincerely,", "Mono", 9.5, lead=30)
    d.text(d.ml, sign, "Mono", 9.5)
    d.text(d.ml, role, "Mono", 9.5)
    _f(d, "letter", re_line, when=dated, org=org)
    for f in facts:
        d.pages[0].facts.append(f)
    return d


# ------------------------------------------------------------ Radiology
def radiology_report(doc_id, *, trust, style, exam, requested: date, performed: date, reported: date,
                     clinical, findings, conclusion, radiologist, accession, received: date | None = None):
    d = Doc(doc_id, "result", margin_l=18 * mm, margin_r=18 * mm)
    c = d.c
    if style == "boxed":
        d.box(d.ml, H - 32 * mm, W - d.ml - d.mr, 16 * mm, stroke=0.3)
        c.setFont("Sans-B", 12)
        c.drawString(d.ml + 3 * mm, H - 23 * mm, f"{trust} - Department of Radiology")
        c.setFont("Sans", 8.5)
        c.drawString(d.ml + 3 * mm, H - 28.5 * mm, f"Accession {accession}    Examination: {exam}")
        d.y = H - 40 * mm
    else:
        c.setFont("Serif-B", 12)
        c.drawString(d.ml, H - 20 * mm, "IMAGING REPORT")
        c.setFont("Serif", 9)
        c.drawRightString(W - d.mr, H - 20 * mm, trust)
        d.y = H - 30 * mm
        d.text(d.ml, f"Examination: {exam}", "Serif-B", 10)
        d.text(d.ml, f"Accession no: {accession}", "Serif", 9)
    fnt = "Sans" if style == "boxed" else "Serif"
    for k, v in [("Patient", f"{P['first']} {P['last']}  DOB {uk_date(P['dob'])}  NHS {fmt_nhs(P['nhs'])}"),
                 ("Referrer", f"Dr R Patel, {PRACTICE['name']}"), ("Requested", uk_date(requested)),
                 ("Examination date", uk_date(performed)), ("Reported", uk_date(reported))]:
        d.text(d.ml, k, fnt + "-B", 9, advance=False)
        d.text(d.ml + 38 * mm, v, fnt, 9)
    d.printed_date(performed)
    d.printed_date(reported)
    d.y -= 6
    for title, body in [("Clinical details", clinical), ("Findings", findings), ("Conclusion", conclusion)]:
        d.text(d.ml, title, fnt + "-B", 10, lead=14)
        d.para(d.ml, body, W - d.ml - d.mr, fnt, 10, lead=13.5)
        d.y -= 6
    d.text(d.ml, f"Reported by {radiologist}", fnt + "-I", 9)
    _f(d, "result", conclusion, when=performed, test=exam)
    if received:
        stamp_received(d, received, x=W - 80 * mm, y=H - 95 * mm)
    return d


# ------------------------------------------------------------ Histology
def histology_report(doc_id, *, lab, specimen_no, received_lab: date, reported: date, specimen, macro, micro,
                     diagnosis, pathologist, snomed):
    d = Doc(doc_id, "result", margin_l=16 * mm, margin_r=16 * mm)
    c = d.c
    c.setFont("Cond-B", 12)
    c.drawString(d.ml, H - 18 * mm, f"{lab.upper()} - CELLULAR PATHOLOGY REPORT")
    d.y = H - 28 * mm
    grid = [("Patient", f"{P['last'].upper()}, {P['first']}"), ("DOB", uk_date(P["dob"])), ("NHS", fmt_nhs(P["nhs"])),
            ("Specimen no", specimen_no), ("Received", uk_date(received_lab)), ("Authorised", uk_date(reported))]
    for i in range(0, 6, 3):
        x = d.ml
        for k, v in grid[i:i + 3]:
            d.text(x, f"{k}: ", "Cond-B", 8.5, advance=False)
            d.text(x + 22 * mm, v, "Cond", 8.5, advance=False)
            x += 60 * mm
        d.y -= 12
    d.printed_date(reported)
    d.y -= 6
    for title, body in [("SPECIMEN", specimen), ("MACROSCOPY", macro), ("MICROSCOPY", micro)]:
        d.text(d.ml, title, "Cond-B", 9, lead=13)
        d.para(d.ml, body, W - d.ml - d.mr, "Cond", 9, lead=12)
        d.y -= 5
    d.text(d.ml, "DIAGNOSIS", "Cond-B", 9.5, lead=13)
    d.para(d.ml, diagnosis, W - d.ml - d.mr, "Cond-B", 9.5)
    d.y -= 4
    d.text(d.ml, f"SNOMED: {snomed}", "Cond", 8)
    d.text(d.ml, f"Reported by {pathologist}", "Cond", 8.5)
    _f(d, "result", diagnosis, when=reported, test="Histology")
    return d


# ------------------------------------------------------------ Emergency department
def ed_letter(doc_id, *, trust, attended: date, arrival, discharged, complaint, triage, diagnosis, treatment,
              followup, clinician, xray=None):
    d = Doc(doc_id, "hospital_letter", margin_l=14 * mm, margin_r=14 * mm)
    c = d.c
    c.setFont("Sans-B", 11)
    c.drawString(d.ml, H - 16 * mm, "EMERGENCY DEPARTMENT ATTENDANCE - GP NOTIFICATION")
    c.setFont("Sans", 8.5)
    c.drawString(d.ml, H - 21 * mm, trust)
    c.drawRightString(W - d.mr, H - 16 * mm, f"Attendance {attended.strftime('%y')}/ED/{random.Random(doc_id).randint(10000, 99999)}")
    d.y = H - 30 * mm
    rows = [("Patient", f"{P['first']} {P['last']}"), ("DOB", uk_date(P["dob"])), ("NHS No", fmt_nhs(P["nhs"])),
            ("Arrival", f"{uk_date(attended)} {arrival}"), ("Departure", discharged), ("Triage category", triage),
            ("Seen by", clinician)]
    for k, v in rows:
        d.text(d.ml, k, "Sans-B", 8.5, advance=False)
        d.text(d.ml + 36 * mm, v, "Sans", 8.5)
    d.printed_date(attended)
    d.y -= 4
    for k, v in [("Presenting complaint", complaint), ("Investigations", xray or "None"), ("Diagnosis", diagnosis),
                 ("Treatment", treatment), ("Follow up", followup)]:
        d.need(14 * mm)
        d.text(d.ml, k, "Sans-B", 9)
        d.para(d.ml + 4 * mm, v, W - d.ml - d.mr - 4 * mm, "Sans", 9)
        d.y -= 3
    _f(d, "letter", f"ED attendance: {diagnosis}", quote=diagnosis, when=attended)
    return d


# ------------------------------------------------------------ NHS 111
def nhs111_report(doc_id, *, call_date: date, time, symptoms, disposition, advice, sent: date):
    d = Doc(doc_id, "ooh", margin_l=16 * mm, margin_r=16 * mm)
    c = d.c
    c.setFont("Sans-B", 12)
    c.drawString(d.ml, H - 18 * mm, "NHS 111 - Post Event Message")
    c.setFont("Sans", 8.5)
    c.drawString(d.ml, H - 23 * mm, "Regional 111 service (invented). Sent to registered practice.")
    d.y = H - 34 * mm
    for k, v in [("Patient", f"{P['first']} {P['last']} ({uk_date(P['dob'])})"), ("NHS number", fmt_nhs(P["nhs"])),
                 ("Call started", f"{uk_date(call_date)} {time}"), ("Pathway", symptoms),
                 ("Final disposition", disposition), ("Care advice given", advice)]:
        d.text(d.ml, k, "Sans-B", 9, advance=False)
        d.para(d.ml + 40 * mm, v, W - d.ml - d.mr - 40 * mm, "Sans", 9)
        d.y -= 2
    d.printed_date(call_date)
    d.y -= 6
    d.text(d.ml, f"Message sent to GP practice {uk_date(sent)}", "Sans-I", 8.5)
    d.page.availability.append({"kind": "sent_to_gp", "date": sent.isoformat(), "quote": f"sent to GP practice {uk_date(sent)}"})
    _f(d, "phone_consultation", f"NHS 111: {symptoms}", quote=symptoms, when=call_date, disposition=disposition)
    return d


# ------------------------------------------------------------ Screening letters
def screening_letter(doc_id, *, programme, dated: date, result, body, sender):
    d = Doc(doc_id, "letters", margin_l=22 * mm, margin_r=22 * mm)
    c = d.c
    c.setFont("Sans-B", 13)
    c.drawString(d.ml, H - 22 * mm, programme)
    c.setFont("Sans", 9)
    c.drawString(d.ml, H - 27 * mm, sender)
    d.y = H - 44 * mm
    d.text(d.ml, f"{P['title']} {P['first']} {P['last']}", "Sans", 10)
    for ln in P["address"]:
        d.text(d.ml, ln, "Sans", 10)
    d.y -= 10
    d.text(d.ml, f"Date: {long_date(dated)}", "Sans", 10)
    d.text(d.ml, f"NHS number: {fmt_nhs(P['nhs'])}", "Sans", 10, lead=20)
    d.printed_date(dated)
    d.text(d.ml, f"Dear {P['title']} {P['last']}", "Sans", 10.5, lead=16)
    d.text(d.ml, result, "Sans-B", 11, lead=18)
    for para in body:
        d.para(d.ml, para, W - d.ml - d.mr, "Sans", 10.5, lead=14)
        d.y -= 6
    d.text(d.ml, "cc: your GP", "Sans", 9)
    _f(d, "letter", result, when=dated, programme=programme)
    return d


# ------------------------------------------------------------ Community pharmacy
def pharmacy_note(doc_id, *, dated: date, pharmacy, reason, advice, supplied):
    d = Doc(doc_id, "letters")
    c = d.c
    c.setFont("Sans-B", 11)
    c.drawString(d.ml, H - 20 * mm, "Community Pharmacy Consultation - notification to GP")
    c.setFont("Sans", 9)
    c.drawString(d.ml, H - 25 * mm, pharmacy)
    d.y = H - 38 * mm
    for k, v in [("Patient", f"{P['first']} {P['last']}"), ("DOB", uk_date(P["dob"])), ("Date of consultation", uk_date(dated)),
                 ("Reason", reason), ("Advice", advice), ("Supplied", supplied)]:
        d.text(d.ml, k, "Sans-B", 9.5, advance=False)
        d.para(d.ml + 45 * mm, v, W - d.ml - d.mr - 45 * mm, "Sans", 9.5)
        d.y -= 2
    d.printed_date(dated)
    _f(d, "letter", f"Pharmacy consultation: {reason}", quote=reason, when=dated)
    return d


# ------------------------------------------------------------ Optician
def optician_letter(doc_id, *, dated: date, findings, request):
    d = Doc(doc_id, "letters", margin_l=20 * mm, margin_r=20 * mm)
    c = d.c
    c.setFont("Serif-B", 14)
    c.drawString(d.ml, H - 22 * mm, "Clearview Opticians")
    c.setFont("Serif-I", 9)
    c.drawString(d.ml, H - 27 * mm, "12 Market Street, Northam NM1 2AB - Optometrists")
    d.y = H - 44 * mm
    _addr = ["Dr R Patel", PRACTICE["name"], *PRACTICE["address"]]
    for ln in _addr:
        d.text(d.ml, ln, "Serif", 10)
    d.y -= 8
    d.text(d.ml, uk_date(dated), "Serif", 10, lead=18)
    d.printed_date(dated)
    d.text(d.ml, f"Re: {P['first']} {P['last']}, DOB {uk_date(P['dob'])}", "Serif-B", 10, lead=16)
    d.para(d.ml, findings, W - d.ml - d.mr, "Serif", 10.5, lead=14)
    d.y -= 6
    d.para(d.ml, request, W - d.ml - d.mr, "Serif", 10.5, lead=14)
    d.y -= 14
    d.text(d.ml, "A. Kowalski MCOptom", "Serif", 10)
    _f(d, "letter", findings[:60], quote=findings.split(".")[0], when=dated)
    return d


# ------------------------------------------------------------ Fit note copy
def fit_note(doc_id, *, dated: date, condition, weeks, clinician):
    d = Doc(doc_id, "gp_record", margin_l=18 * mm, margin_r=18 * mm)
    c = d.c
    d.box(d.ml, H - 120 * mm, W - d.ml - d.mr, 100 * mm, stroke=0.2, lw=1)
    c.setFont("Sans-B", 13)
    c.drawString(d.ml + 5 * mm, H - 30 * mm, "Statement of fitness for work (practice copy)")
    d.y = H - 42 * mm
    for k, v in [("Patient", f"{P['title']} {P['first']} {P['last']}"), ("Date of birth", uk_date(P["dob"])),
                 ("I assessed your case on", uk_date(dated)), ("Condition", condition),
                 ("Advice", "You are not fit for work."), ("This will be the case for", f"{weeks} weeks"),
                 ("Doctor", clinician), ("Date of statement", uk_date(dated))]:
        d.text(d.ml + 5 * mm, k, "Sans-B", 10, advance=False)
        d.text(d.ml + 75 * mm, v, "Sans", 10, lead=17)
    d.printed_date(dated)
    _f(d, "task", f"Fit note: {condition}", quote=condition, when=dated, weeks=weeks)
    return d


# ------------------------------------------------------------ ECG
def ecg(doc_id, *, recorded: date, time, rate, pr, qrs, qt, qtc, interp, where):
    d = Doc(doc_id, "result", margin_l=10 * mm, margin_r=10 * mm)
    c = d.c
    c.setFont("Mono-B", 9)
    c.drawString(d.ml, H - 14 * mm, f"{P['last'].upper()}, {P['first'].upper()}   ID {P['hospital_numbers']['northam']}   "
                                    f"DOB {uk_date(P['dob'])}   {recorded.strftime('%d-%b-%Y').upper()} {time}")
    c.setFont("Mono", 8.5)
    c.drawString(d.ml, H - 19 * mm, f"Vent rate {rate} BPM   PR int {pr} ms   QRS dur {qrs} ms   QT/QTc {qt}/{qtc} ms")
    c.drawString(d.ml, H - 23.5 * mm, interp)
    c.drawString(d.ml, H - 28 * mm, f"Location: {where}   25 mm/s  10 mm/mV  0.05-150 Hz")
    top, left, gw, gh = H - 34 * mm, d.ml, W - d.ml - d.mr, 150 * mm
    c.setStrokeColorRGB(0.95, 0.75, 0.75)
    c.setLineWidth(0.2)
    for i in range(int(gw / mm) + 1):
        c.setLineWidth(0.5 if i % 5 == 0 else 0.15)
        c.line(left + i * mm, top, left + i * mm, top - gh)
    for j in range(int(gh / mm) + 1):
        c.setLineWidth(0.5 if j % 5 == 0 else 0.15)
        c.line(left, top - j * mm, left + gw, top - j * mm)
    c.setStrokeGray(0)
    c.setLineWidth(0.7)
    leads = ["I", "II", "III", "aVR", "aVL", "aVF"]
    beat = 60.0 / rate * 25 * mm
    for r, lead in enumerate(leads):
        base = top - 14 * mm - r * 24 * mm
        c.setFont("Mono", 7)
        c.drawString(left + 1 * mm, base + 6 * mm, lead)
        pts, x = [], left + 8 * mm
        sign = -1 if lead == "aVR" else 1
        while x < left + gw - 2 * mm:
            t = ((x - left) % beat) / beat
            y = 0.0
            y += 1.2 * math.exp(-((t - 0.18) / 0.03) ** 2)
            y += sign * 9 * math.exp(-((t - 0.3) / 0.008) ** 2) - 1.5 * math.exp(-((t - 0.32) / 0.01) ** 2)
            y += 2.4 * math.exp(-((t - 0.55) / 0.05) ** 2)
            pts.append((x, base + y * mm))
            x += 0.4
        p = c.beginPath()
        p.moveTo(*pts[0])
        for pt in pts[1:]:
            p.lineTo(*pt)
        c.drawPath(p)
    d.y = top - gh - 8 * mm
    d.printed_date(recorded)
    _f(d, "result", interp, when=recorded, test="ECG", rate=rate)
    return d


# ------------------------------------------------------------ Fax cover sheet
def fax_cover(doc_id, *, sent: date, time, sender, pages, subject):
    d = Doc(doc_id, "admin", margin_l=24 * mm)
    c = d.c
    c.setFont("Sans-B", 40)
    c.drawString(d.ml, H - 40 * mm, "FAX")
    d.y = H - 60 * mm
    for k, v in [("To", f"{PRACTICE['name']} - Fax 01632 960219"), ("From", sender), ("Date", f"{uk_date(sent)} {time}"),
                 ("Pages (inc. this)", str(pages)), ("Re", subject)]:
        d.text(d.ml, k, "Sans-B", 13, advance=False)
        d.text(d.ml + 50 * mm, v, "Sans", 13, lead=22)
    d.y -= 20
    d.para(d.ml, "CONFIDENTIAL. If you have received this fax in error please telephone the sender and destroy all pages.",
           W - d.ml - 24 * mm, "Sans-I", 10)
    d.printed_date(sent)
    return d


# ------------------------------------------------------------ Private hospital letter
def kingsmead_letter(doc_id, *, dated: date, consultant, specialty, body, plan):
    d = Doc(doc_id, "hospital_letter", margin_l=25 * mm, margin_r=25 * mm)
    c = d.c
    c.setFont("Serif-I", 18)
    c.drawCentredString(W / 2, H - 22 * mm, "Kingsmead Private Hospital")
    c.setFont("Serif", 8.5)
    c.drawCentredString(W / 2, H - 27 * mm, "Kingsmead Road, Northam NM3 6FA  -  Private patients' secretaries 01632 960890")
    c.line(d.ml, H - 30 * mm, W - d.mr, H - 30 * mm)
    d.y = H - 42 * mm
    d.text(d.ml, f"PRIVATE AND CONFIDENTIAL", "Serif-B", 9.5, lead=16)
    for ln in ["Dr R Patel", PRACTICE["name"], *PRACTICE["address"]]:
        d.text(d.ml, ln, "Serif", 10)
    d.y -= 8
    d.text(d.ml, long_date(dated), "Serif", 10, lead=18)
    d.printed_date(dated)
    d.text(d.ml, "Dear Rakesh,", "Serif", 10.5, lead=16)
    d.text(d.ml, f"{P['first']} {P['last']}  ({uk_date(P['dob'])})", "Serif-B", 10.5, lead=16)
    for para in body:
        d.para(d.ml, para, W - d.ml - d.mr, "Serif", 10.5, lead=14)
        d.y -= 6
    d.text(d.ml, "Plan", "Serif-B", 10.5)
    for ln in plan:
        d.para(d.ml + 5 * mm, ln, W - d.ml - d.mr - 5 * mm, "Serif", 10.5)
    d.y -= 14
    d.text(d.ml, "With best wishes,", "Serif", 10.5, lead=26)
    d.text(d.ml, consultant, "Serif-B", 10.5)
    d.text(d.ml, f"Consultant {specialty}", "Serif", 10)
    _f(d, "letter", f"{specialty} private consultation", quote=body[0].split(".")[0], when=dated)
    return d


# ------------------------------------------------------------ Community physio
def riverside_letter(doc_id, *, dated: date, service, clinician, sessions, summary, outcome):
    d = Doc(doc_id, "hospital_letter", margin_l=16 * mm, margin_r=16 * mm)
    c = d.c
    d.box(d.ml, H - 26 * mm, 4 * mm, 12 * mm, fill=0.3, stroke=0.3)
    c.setFont("Sans-B", 12)
    c.drawString(d.ml + 7 * mm, H - 19 * mm, "Riverside Community Health Services")
    c.setFont("Sans", 8.5)
    c.drawString(d.ml + 7 * mm, H - 24 * mm, service)
    d.y = H - 36 * mm
    d.text(d.ml, f"Discharge summary - {uk_date(dated)}", "Sans-B", 10, lead=16)
    d.printed_date(dated)
    for k, v in [("Patient", f"{P['first']} {P['last']}, {uk_date(P['dob'])}, NHS {fmt_nhs(P['nhs'])}"),
                 ("Clinician", clinician), ("Sessions attended", sessions), ("Summary", summary), ("Outcome", outcome)]:
        d.text(d.ml, k, "Sans-B", 9.5)
        d.para(d.ml + 4 * mm, v, W - d.ml - d.mr - 4 * mm, "Sans", 9.5)
        d.y -= 3
    _f(d, "letter", f"{service} discharge", quote=outcome.split(".")[0], when=dated)
    return d


# ------------------------------------------------------------ Westvale pathology (table style)
def westvale_path(doc_id, *, collected: date, reported: date, lab_no, rows, comment=None, requester="Dr S Okafor"):
    d = Doc(doc_id, "result", margin_l=14 * mm, margin_r=14 * mm)
    c = d.c
    d.box(d.ml, H - 24 * mm, W - d.ml - d.mr, 10 * mm, fill=0.2, stroke=0.2)
    c.setFillGray(1)
    c.setFont("Sans-B", 11)
    c.drawString(d.ml + 3 * mm, H - 20.5 * mm, "Westvale Laboratory Medicine")
    c.drawRightString(W - d.mr - 3 * mm, H - 20.5 * mm, f"Report {lab_no}")
    c.setFillGray(0)
    d.y = H - 32 * mm
    d.text(d.ml, f"{P['last'].upper()}, {P['first']}   DOB {uk_date(P['dob'])}   NHS {fmt_nhs(P['nhs'])}", "Sans-B", 9)
    d.text(d.ml, f"Requested by {requester}   Collected {uk_date(collected)}   Reported {uk_date(reported)}", "Sans", 8.5, lead=16)
    d.printed_date(collected)
    d.printed_date(reported)
    widths = [60, 25, 25, 40, 20]
    heads = ["Investigation", "Result", "Units", "Reference interval", ""]
    x = d.ml
    for h_, w_ in zip(heads, widths):
        d.box(x, d.y - 2 * mm, w_ * mm, 6 * mm, fill=0.9, stroke=0.5)
        d.text(x + 1.5 * mm, h_, "Sans-B", 8.5, y=d.y)
        x += w_ * mm
    d.y -= 6 * mm
    for test, val, unit, rng, flag in rows:
        x = d.ml
        for v, w_ in zip([test, val, unit, rng, flag], widths):
            d.box(x, d.y - 2 * mm, w_ * mm, 6 * mm, stroke=0.6)
            d.text(x + 1.5 * mm, v, "Sans-B" if flag and v == val else "Sans", 8.5, y=d.y)
            x += w_ * mm
        d.y -= 6 * mm
        _f(d, "result", f"{val} {unit}".strip(), quote=f"{test} {val}", when=reported, test=test, flag=flag)
    if comment:
        d.y -= 4
        d.para(d.ml, f"Comment: {comment}", W - d.ml - d.mr, "Sans-I", 9)
    return d


# ------------------------------------------------------------ Docman-style coversheet strip (typed header)
def docman_header_overlay(doc_id, *, filed_by, filed_on: date, doc_type, received: date):
    """A one-page PDF with only a typed header strip, merged over a scanned page.
    Gives the planted 'typed header, scanned body' page."""
    d = Doc(doc_id, "admin")
    d.box(10 * mm, H - 16 * mm, W - 20 * mm, 9 * mm, fill=0.95, stroke=0.4)
    d.c.setFont("Sans-B", 8)
    d.c.drawString(13 * mm, H - 12.5 * mm, f"Brookvale Surgery document store  |  Type: {doc_type}  |  Received {uk_date(received)}  "
                                           f"|  Filed by {filed_by} {uk_date(filed_on)}")
    return d
