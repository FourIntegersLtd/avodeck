"""GP record prints in the two common English styles.

* EMIS-style "Brief summary", "Full summary" (Consultations > Print), medication
  issue history, appointment history and task list prints.
* SystmOne-style subject access print (journal exported to a word processor).

Layouts follow how these prints usually look; no supplier logos, no real codes.
Clinical codes shown in the SystmOne style are invented placeholders.
Every fact drawn is recorded on the page it lands on.
"""
from __future__ import annotations

import hashlib
from datetime import date

from reportlab.lib.units import mm

from common import Doc, W, H, emis_date, s1_date, fmt_nhs
from story import PATIENT, PRACTICE, CLINICIANS, Consultation, Result

CTYPE_KIND = {"Surgery Consultation": "consultation", "Telephone call": "phone_consultation",
              "Home visit": "home_visit", "Administration": "task", "Document": "letter",
              "Video consultation": "consultation"}


def age_on(dob: date, d: date) -> int:
    return d.year - dob.year - ((d.month, d.day) < (dob.month, dob.day))


def fake_code(term: str) -> str:
    h = hashlib.sha1(term.encode()).hexdigest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz0123456789"
    n = int(h[:10], 16)
    s = ""
    for _ in range(3):
        s += alphabet[n % len(alphabet)]
        n //= len(alphabet)
    return "Xa" + s


def result_flag_text(flag: str) -> str:
    return {"L": "Below low reference limit", "H": "Above high reference limit"}.get(flag, "")


def _consult_fact(d: Doc, c: Consultation, field: str, value: str):
    """Record the fact for one drawn row of a consultation."""
    who = CLINICIANS[c.who][0]
    planted = c.planted.get(field)
    if field == "problem":
        d.fact(CTYPE_KIND.get(c.ctype, "consultation"), value, event_date=c.d, clinician=who,
               clinician_role=CLINICIANS[c.who][1], history=c.history, plan=c.plan, planted=planted)
    elif field in ("exam",):
        d.fact("examination", value, event_date=c.d, clinician=who, planted=planted)
    elif field == "value":
        d.fact("examination", value, event_date=c.d, clinician=who, planted=planted)
    elif field == "plan":
        if planted:   # planted follow-up advice inside the plan
            d.fact("safety_netting", "review 3/52 if no better", event_date=c.d, clinician=who, planted=planted)
    elif field == "safety_net":
        d.fact("safety_netting", value, event_date=c.d, clinician=who, planted=planted)
    elif field == "referral":
        d.fact("referral", value, event_date=c.d, clinician=who, planted=planted)
    elif field == "med":
        d.fact("prescription", value, event_date=c.d, clinician=who, planted=planted)


# ---------------------------------------------------------------- EMIS style
class EmisPrint:
    def __init__(self, doc_id: str, kind: str, printed: date, printed_by: str, source_type="gp_record"):
        self.kind = kind
        self.printed = printed
        self.printed_by = printed_by
        self.doc = Doc(doc_id, source_type, margin_t=14 * mm, margin_b=22 * mm,
                       header=self._header, footer=self._footer)

    def _header(self, d: Doc, n: int):
        p = PATIENT
        c = d.c
        y = H - 12 * mm
        c.setFont("Sans", 7.5)
        c.drawString(d.ml, y, f"{PRACTICE['name']}, {', '.join(PRACTICE['address'])}")
        c.drawRightString(W - d.mr, y, self.kind)
        y -= 6 * mm
        c.setFont("Sans-B", 11)
        c.drawString(d.ml, y, f"{p['last'].upper()}, {p['first']} ({p['title']})")
        y -= 4.6 * mm
        c.setFont("Sans", 8.5)
        c.drawString(d.ml, y, f"Born {emis_date(p['dob'])} ({age_on(p['dob'], self.printed)}y)    "
                              f"Gender {p['sex']}    NHS No. {fmt_nhs(p['nhs'])}")
        c.drawRightString(W - d.mr, y, f"Usual GP {p['usual_gp']}")
        y -= 3 * mm
        c.setLineWidth(0.8)
        c.line(d.ml, y, W - d.mr, y)
        return y - 6 * mm

    def _footer(self, d: Doc, n: int):
        c = d.c
        c.setFont("Sans", 7)
        c.drawString(d.ml, 16.5 * mm, "CONFIDENTIAL - PATIENT IDENTIFIABLE INFORMATION")
        c.drawString(d.ml, 13 * mm, f"Printed {emis_date(self.printed)} by {self.printed_by}")
        c.drawRightString(W - d.mr, 13 * mm, f"Page {n}")

    def section(self, title: str):
        d = self.doc
        d.need(16 * mm)
        d.y -= 2
        d.box(d.ml, d.y - 2.2 * mm, W - d.ml - d.mr, 6 * mm, fill=0.88, stroke=0.88)
        d.text(d.ml + 2 * mm, title, "Sans-B", 9.5, lead=13)
        d.y -= 2

    def table(self, cols, rows, size=8.5, on_row=None):
        d = self.doc
        xs, x = [], d.ml
        for _, w in cols:
            xs.append(x)
            x += w * mm
        d.need(10 * mm)
        for (head, _), xx in zip(cols, xs):
            d.text(xx, head, "Sans-B", size, advance=False)
        d.y -= size * 1.5
        for r in rows:
            wrapped = [d.wrap_lines(v, cols[i][1] * mm - 2 * mm, "Sans", size) for i, v in enumerate(r)]
            nlines = max(len(w) for w in wrapped)
            if d.y - (nlines * size * 1.3 + 2) < d.mb:
                d.page_break()
                for (head, _), xx in zip(cols, xs):
                    d.text(xx, head, "Sans-B", size, advance=False)
                d.y -= size * 1.5
            y0 = d.y
            for i, lines in enumerate(wrapped):
                for j, ln in enumerate(lines):
                    d.text(xs[i], ln, "Sans", size, y=y0 - j * size * 1.3)
            d.y = y0 - nlines * size * 1.3 - 1.5
            if on_row:
                on_row(r)

    def patient_details(self, registered: date):
        p, d = PATIENT, self.doc
        self.section("Patient Details")
        for label, val in [("Address", ", ".join(p["address"])), ("Telephone", p["phone"]),
                           ("Registered", emis_date(registered)), ("Registration type", "Regular")]:
            d.labelled(d.ml, d.ml + 32 * mm, label, val, 120 * mm, size=8.5)

    def problems(self, active, sig_past, minor_past):
        self.section("Active Problems")
        self.table([("Date", 28), ("Problem", 100), ("Last entry", 40)],
                   [[emis_date(a), b, emis_date(c)] for a, b, c in active] or [["", "No active problems", ""]])
        self.section("Significant Past Problems")
        self.table([("Date", 28), ("Problem", 100), ("End date", 40)],
                   [[emis_date(a), b, emis_date(c)] for a, b, c in sig_past] or [["", "None recorded", ""]])
        if minor_past is not None:
            self.section("Minor Past Problems")
            self.table([("Date", 28), ("Problem", 100), ("End date", 40)],
                       [[emis_date(a), b, emis_date(c)] for a, b, c in minor_past] or [["", "None recorded", ""]])

    def medication(self, current, past=None, title="Current Medication"):
        def rec(r):
            self.doc.fact("prescription", r[0], event_date=None, dose=r[1], quantity=r[2], last_issued=r[3], issue_type=r[4])
        self.section(title)
        self.table([("Drug", 62), ("Dosage", 56), ("Quantity", 22), ("Last issued", 24), ("Type", 14)],
                   [[m["drug"], m["dose"], m["qty"], emis_date(m["last"]), m["type"]] for m in current], on_row=rec)
        if past is not None:
            self.section("Past Medication")
            self.table([("Drug", 62), ("Dosage", 56), ("Quantity", 22), ("Issued", 24), ("Type", 14)],
                       [[m["drug"], m["dose"], m["qty"], emis_date(m["last"]), m["type"]] for m in past], on_row=rec)

    def issues(self, issues):
        """Medication issue history: one row per issue."""
        def rec(r):
            self.doc.fact("prescription", r[1], quote=f"{r[1]}", event_date=None, issue_date=r[0], quantity=r[3])
        self.section("Medication Issues")
        self.table([("Issue date", 24), ("Drug", 64), ("Dosage", 50), ("Quantity", 20), ("Issued by", 26)],
                   [[emis_date(i["d"]), i["drug"], i["dose"], i["qty"], i["by"]] for i in issues], size=8, on_row=rec)

    def appointments(self, appts):
        def rec(r):
            if r[4] == "Did not attend":
                self.doc.fact("missed_appointment", "Did not attend", quote=f"{r[0]} {r[1]}",
                              event_date=None, slot=r[2], with_=r[3])
        self.section("Appointment History")
        self.table([("Date", 24), ("Time", 14), ("Slot type", 46), ("With", 40), ("Outcome", 42)],
                   [[emis_date(a["d"]), a["t"], a["slot"], a["with"], a["outcome"]] for a in appts], size=8, on_row=rec)

    def tasks(self, tasks):
        def rec(r):
            self.doc.fact("task", r[2], event_date=None, created=r[0], status=r[4])
        self.section("Tasks")
        self.table([("Created", 24), ("Type", 28), ("Description", 74), ("Assigned to", 28), ("Status", 20)],
                   [[emis_date(t["d"]), t["type"], t["desc"], t["to"], t["status"]] for t in tasks], size=8, on_row=rec)

    def allergies(self, items):
        self.section("Allergies & Sensitivities")
        d = self.doc
        for it in items:
            d.text(d.ml, it, "Sans", 8.5)

    def values(self, rows):
        self.section("Recent Values")
        self.table([("Date", 28), ("Value", 70), ("Result", 70)],
                   [[emis_date(a), b, c] for a, b, c in rows])

    def consultations_heading(self):
        self.section("Consultations")

    def consultation(self, c: Consultation):
        d = self.doc
        name, role = CLINICIANS[c.who]
        d.need(22 * mm)
        d.y -= 2
        d.text(d.ml, f"{emis_date(c.d)}   {c.time}", "Sans-B", 9, advance=False)
        d.text(d.ml + 34 * mm, c.ctype, "Sans-B", 9, advance=False)
        d.right(W - d.mr, f"{PRACTICE['name']}", "Sans", 8)
        d.y -= 11
        d.text(d.ml + 34 * mm, f"{name} ({role})", "Sans-I", 8.5)
        d.printed_date(c.d)
        xl, xv, wv = d.ml + 4 * mm, d.ml + 34 * mm, W - d.mr - (d.ml + 34 * mm)
        rows = [("Problem", c.problem, "problem"), ("History", c.history, None), ("Examination", c.exam, "exam"),
                ("Comment", c.comment, None)]
        for lab, val in c.values:
            rows.append((lab, val, "value"))
        rows += [("Plan", c.plan, "plan"), ("Safety netting", c.safety_net, "safety_net"),
                 ("Referral", c.referral, "referral")]
        if not c.problem:
            _consult_fact(d, c, "problem", c.ctype)
        for label, val, field in rows:
            if val:
                d.labelled(xl, xv, label, val, wv, size=9.5)
                if field:
                    _consult_fact(d, c, field, val)
        for m in c.meds:
            d.labelled(xl, xv, "Medication", f"{m.name}  {m.dose}  {m.qty}", wv, size=9.5)
            _consult_fact(d, c, "med", m.name)
        d.y -= 7
        d.hline(gray=0.82, w=0.4)
        d.y -= 5

    def document_entry(self, when: date, title: str, source: str, filed_by: str, filed_on: date, comment: str):
        """A scanned/electronic document filed into the record (Docman-style)."""
        d = self.doc
        d.need(16 * mm)
        d.y -= 2
        d.text(d.ml, emis_date(when), "Sans-B", 9, advance=False)
        d.text(d.ml + 34 * mm, "Document", "Sans-B", 9, advance=False)
        d.right(W - d.mr, source, "Sans", 8)
        d.y -= 11
        d.labelled(d.ml + 4 * mm, d.ml + 34 * mm, "Attachment", title, W - d.mr - d.ml - 34 * mm, size=8.5)
        d.labelled(d.ml + 4 * mm, d.ml + 34 * mm, "Filed", f"Filed by {filed_by} on {emis_date(filed_on)}: \"{comment}\"",
                   W - d.mr - d.ml - 34 * mm, size=8.5)
        d.printed_date(when)
        d.fact("letter", title, event_date=when, source=source)
        d.page.availability.append({"kind": "filed", "date": filed_on.isoformat(), "for": title,
                                    "quote": f"Filed by {filed_by} on {emis_date(filed_on)}"})
        d.y -= 3
        d.hline(gray=0.82, w=0.4)
        d.y -= 2

    def result(self, r: Result):
        d = self.doc
        filer = CLINICIANS[r.filed_by][0]
        d.need(26 * mm)
        d.y -= 2
        d.text(d.ml, f"{emis_date(r.reported)}", "Sans-B", 9, advance=False)
        d.text(d.ml + 34 * mm, "Pathology Report", "Sans-B", 9, advance=False)
        d.right(W - d.mr, f"Lab no. {r.lab_no}", "Sans", 8)
        d.y -= 11
        d.text(d.ml + 34 * mm, f"{r.panel}  (requested {emis_date(r.requested)})", "Sans-I", 8.5)
        d.printed_date(r.reported)
        for ln in r.lines:
            d.need(12)
            flag = result_flag_text(ln.flag)
            d.text(d.ml + 34 * mm, f"{ln.test}", "Sans", 8.5, advance=False)
            d.text(d.ml + 80 * mm, f"{ln.value} {ln.unit}", "Sans-B" if ln.flag else "Sans", 8.5, advance=False)
            d.text(d.ml + 104 * mm, f"({ln.rng})", "Sans", 8.5, advance=False)
            d.text(d.ml + 128 * mm, flag, "Sans-I", 8, lead=11)
            d.fact("result", f"{ln.value} {ln.unit}", quote=f"{ln.test} {ln.value} {ln.unit}", event_date=r.reported,
                   test=ln.test, flag=ln.flag, lab_no=r.lab_no, planted=r.planted if ln.flag else None)
        d.text(d.ml + 34 * mm, f"Filed by {filer} on {emis_date(r.filed)}:  \"{r.comment}\"", "Sans", 8.5)
        d.page.availability.append({"kind": "filed", "date": r.filed.isoformat(), "for": r.lab_no,
                                    "quote": f"Filed by {filer} on {emis_date(r.filed)}"})
        d.y -= 3
        d.hline(gray=0.82, w=0.4)
        d.y -= 2


# ----------------------------------------------------------- SystmOne style
class SystmOnePrint:
    def __init__(self, doc_id: str, printed: date, printed_by: str, practice: dict | None = None,
                 lab_name: str = "Northam Pathology"):
        self.printed = printed
        self.printed_by = printed_by
        self.practice = practice or PRACTICE
        self.lab_name = lab_name
        self.doc = Doc(doc_id, "gp_record", margin_l=20 * mm, margin_r=20 * mm, margin_t=18 * mm,
                       margin_b=20 * mm, header=self._header, footer=self._footer)

    def _header(self, d: Doc, n: int):
        p = PATIENT
        c = d.c
        y = H - 14 * mm
        if n == 1:
            c.setFont("Sans-B", 14)
            c.drawString(d.ml, y, "Patient Record")
            y -= 7 * mm
            rows = [("Name", f"{p['title']} {p['first']} {p['last']}"), ("Date of birth", s1_date(p["dob"])),
                    ("NHS number", fmt_nhs(p["nhs"])), ("Address", ", ".join(self.practice.get("patient_address", p["address"]))),
                    ("Registered practice", f"{self.practice['name']} ({self.practice['ods']})")]
            box_h = len(rows) * 5.2 * mm + 3 * mm
            d.box(d.ml, y - box_h + 4 * mm, W - d.ml - d.mr, box_h, stroke=0.4)
            y -= 1 * mm
            for k, v in rows:
                c.setFont("Sans-B", 9)
                c.drawString(d.ml + 3 * mm, y, k)
                c.setFont("Sans", 9)
                c.drawString(d.ml + 45 * mm, y, v)
                y -= 5.2 * mm
            y -= 3 * mm
            c.setFont("Sans-I", 8)
            c.drawString(d.ml, y, f"Subject access request. Record printed {s1_date(self.printed)} "
                                  f"by {self.printed_by}. Entries in date order.")
            return y - 8 * mm
        c.setFont("Sans", 8)
        c.drawString(d.ml, y, f"{p['title']} {p['first']} {p['last']}   DOB {s1_date(p['dob'])}   "
                              f"NHS {fmt_nhs(p['nhs'])}")
        c.drawRightString(W - d.mr, y, "Patient Record")
        return y - 8 * mm

    def _footer(self, d: Doc, n: int):
        d.c.setFont("Sans", 7.5)
        d.c.drawRightString(W - d.mr, 13 * mm, f"Page {n}")

    def _entry_head(self, when: date, time: str, who: str):
        d = self.doc
        d.need(20 * mm)
        d.y -= 3
        d.box(d.ml, d.y - 1.6 * mm, W - d.ml - d.mr, 5.4 * mm, fill=0.92, stroke=0.92)
        d.text(d.ml + 1.5 * mm, f"{s1_date(when)} {time} - {who} at {self.practice['name']}", "Sans-B", 8.8, lead=13)
        d.printed_date(when)

    def _line(self, s: str, bold_prefix: str | None = None):
        d = self.doc
        x, w = d.ml + 4 * mm, W - d.mr - d.ml - 4 * mm
        if bold_prefix:
            s = f"{bold_prefix} {s}"
        d.para(x, s, w, "Sans", 8.8)

    def consultation(self, c: Consultation):
        name, role = CLINICIANS[c.who]
        role_s1 = {"GP": "General Medical Practitioner", "GP Registrar": "GP Registrar"}.get(role, role)
        self._entry_head(c.d, c.time, f"{name} ({role_s1})")
        mode = {"Surgery Consultation": "Face to face consultation", "Telephone call": "Telephone consultation",
                "Home visit": "Home visit", "Administration": "Administration", "Video consultation": "Video consultation",
                "Document": "Correspondence"}[c.ctype]
        self._line(f"{mode} ({fake_code(mode)})")
        d = self.doc
        if c.problem:
            self._line(f"Problem: {c.problem} ({fake_code(c.problem)})")
            _consult_fact(d, c, "problem", c.problem)
        else:
            _consult_fact(d, c, "problem", mode)
        for label, val, field in [("History:", c.history, None), ("Examination:", c.exam, "exam"),
                                  ("Comment:", c.comment, None)]:
            if val:
                self._line(val, label)
                if field:
                    _consult_fact(d, c, field, val)
        for lab, val in c.values:
            self._line(f"{lab} {val} ({fake_code(lab)})")
            _consult_fact(d, c, "value", val)
        if c.plan:
            self._line(c.plan, "Plan:")
            _consult_fact(d, c, "plan", c.plan)
        if c.safety_net:
            self._line(c.safety_net, "Safety netting advice given:")
            _consult_fact(d, c, "safety_net", c.safety_net)
        if c.referral:
            self._line(f"{c.referral} ({fake_code('referral')})")
            _consult_fact(d, c, "referral", c.referral)
        for m in c.meds:
            self._line(f"{m.name} - {m.qty} - {m.dose.lower()} ({'Repeat' if m.kind == 'repeat' else 'Acute'})",
                       "Medication:")
            _consult_fact(d, c, "med", m.name)

    def result(self, r: Result):
        name, _ = CLINICIANS[r.filed_by]
        self._entry_head(r.reported, "07:58", f"{self.lab_name} (electronic report)")
        self._line(f"{r.panel} - lab ref {r.lab_no}, requested {s1_date(r.requested)}")
        d = self.doc
        for ln in r.lines:
            flag = result_flag_text(ln.flag)
            self._line(f"{ln.test} {ln.value} {ln.unit} [{ln.rng}] ({fake_code(ln.test)}) {flag}".rstrip())
            d.fact("result", f"{ln.value} {ln.unit}", quote=f"{ln.test} {ln.value} {ln.unit}", event_date=r.reported,
                   test=ln.test, flag=ln.flag, lab_no=r.lab_no)
        self._line(f"Result reviewed by {name} on {s1_date(r.filed)}: {r.comment}")
        d.page.availability.append({"kind": "filed", "date": r.filed.isoformat(), "for": r.lab_no,
                                    "quote": f"reviewed by {name} on {s1_date(r.filed)}"})
