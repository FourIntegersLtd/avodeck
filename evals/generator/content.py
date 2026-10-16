"""Background content for the Mrs A record: years of ordinary GP care around the core story.

Seeded, so the same bundle is produced every run.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from story import Consultation, Med, Result, ResultLine

R = random.Random(42)

# ------------------------------------------------------------ consultation templates
# Each template returns a Consultation for a given date. Wording varies per call.
def _pick(*xs):
    return R.choice(xs)


def t_bp_review(d):
    sys_, dia = R.randint(126, 146), R.randint(76, 90)
    return Consultation(d, _pick("09:10", "10:30", "14:40", "16:20"), "Surgery Consultation", _pick("nurse", "hca"),
                        problem="Hypertension review",
                        history=_pick("Annual BP review. Taking amlodipine daily, no side effects.",
                                      "BP check. Home readings 130s/80s. No chest pain or headaches.",
                                      "Hypertension monitoring. Compliant with meds."),
                        values=[("Blood pressure", f"{sys_}/{dia} mmHg"), ("Pulse rate", f"{R.randint(64, 84)} /min")],
                        plan=_pick("Continue amlodipine. Annual bloods booked.", "Continue. Recheck 12 months.",
                                   "Lifestyle advice reinforced. Review 1 yr."))


def t_thyroid(d):
    return Consultation(d, _pick("11:00", "12:10", "15:30"), "Telephone call", _pick("patel", "okafor", "hughes"),
                        problem="Hypothyroidism",
                        history=_pick("TFT result discussed. Feels well on current dose.",
                                      "Annual thyroid review. No symptoms of over or under replacement."),
                        plan="Continue levothyroxine 75mcg. Repeat TFT in 12 months.")


def t_urti(d):
    return Consultation(d, _pick("08:40", "09:20", "17:10"), _pick("Surgery Consultation", "Telephone call"),
                        _pick("patel", "okafor", "locum"), problem=_pick("Upper respiratory tract infection", "Sore throat symptom",
                                                                       "Acute sinusitis", "Cough"),
                        history=_pick("4/7 cough and coryza. Eating and drinking. No SOB.",
                                      "Sore throat 3 days, mild fever. No difficulty swallowing.",
                                      "Facial pain and blocked nose 10 days."),
                        exam=_pick("Chest clear. T 37.4. Sats 98%.", "Throat mildly inflamed, no exudate. FeverPAIN 1.",
                                   "Tender over maxillary sinuses. Afebrile."),
                        plan=_pick("Self care advice. Return if worse.", "Paracetamol, fluids. Delayed script not given.",
                                   "Saline nasal douching. Review if not settling."))


def t_back(d):
    return Consultation(d, "10:10", "Surgery Consultation", _pick("patel", "hughes"), problem="Low back pain",
                        history="Lower back pain 1 week after gardening. No leg symptoms. No red flags.",
                        exam="Paraspinal tenderness L4/5. SLR 80 bilat. Neuro intact.",
                        plan="Advice leaflet, keep active. Analgesia.",
                        meds=[Med("Naproxen 250mg tablets", "One tablet twice a day with food", "28 tablet")])


def t_knee(d):
    return Consultation(d, "15:50", "Surgery Consultation", "patel", problem="Knee pain",
                        history="R knee pain on stairs 2 months. Occasional swelling. No locking.",
                        exam="Mild crepitus R knee, small effusion, full ROM, stable.",
                        plan="X-ray R knee. Physio self-referral. Topical NSAID.",
                        meds=[Med("Ibuprofen 5% gel", "Apply three times a day", "100 gram")])


def t_mood(d):
    return Consultation(d, "13:20", "Telephone call", _pick("okafor", "hughes"), problem="Stress at work",
                        history="Low mood and poor sleep with work pressures. No thoughts of self harm. Supportive husband.",
                        comment="PHQ-9 8.", plan="Self referral talking therapies. Sleep hygiene. Review 4 weeks.",
                        values=[("PHQ-9 score", "8")])


def t_menopause(d):
    return Consultation(d, "10:40", "Surgery Consultation", "okafor", problem="Menopausal symptoms",
                        history="Hot flushes and night sweats, LMP 8 months ago. Wants to discuss HRT.",
                        exam="BP 132/80. BMI 26.", plan="Discussed risks/benefits. Start HRT. Review 3 months.",
                        values=[("Blood pressure", "132/80 mmHg")],
                        meds=[Med("Estradiol 0.06% gel", "Two pumps daily", "80 gram"),
                              Med("Utrogestan 100mg capsules", "One at night", "30 capsule")])


def t_uti(d):
    return Consultation(d, "16:40", "Telephone call", _pick("locum", "pharm", "hughes"), problem="Urinary tract infection",
                        history="Dysuria and frequency 2 days. No loin pain, no fever.",
                        plan="Nitrofurantoin 3 days. MSU if not settling.",
                        meds=[Med("Nitrofurantoin 100mg modified-release capsules", "One twice a day for 3 days", "6 capsule")])


def t_skin(d):
    return Consultation(d, "11:30", "Surgery Consultation", "hughes", problem=_pick("Eczema", "Seborrhoeic keratosis",
                                                                                   "Contact dermatitis"),
                        history="Itchy rash both hands 3 weeks. New washing up liquid.",
                        exam="Dry erythematous patches dorsum of hands, no infection.",
                        plan="Emollients, avoid irritant. Mild steroid 2 weeks.",
                        meds=[Med("Hydrocortisone 1% cream", "Apply thinly twice a day", "30 gram")])


def t_flu(d):
    return Consultation(d, "10:05", "Surgery Consultation", "nurse", problem="Influenza vaccination",
                        history="Seasonal influenza vaccine given L deltoid. Batch recorded. No reaction.")


def t_med_review(d):
    return Consultation(d, "12:30", "Telephone call", "pharm", problem="Medication review",
                        history="Structured medication review. Taking all meds as prescribed. No side effects.",
                        plan="Repeats authorised for 12 months.")


def t_smear(d):
    return Consultation(d, "09:50", "Surgery Consultation", "nurse", problem="Cervical smear",
                        history="Routine cervical screening sample taken. LBC. Results letter to follow.")


def t_bloods(d):
    return Consultation(d, "08:20", "Surgery Consultation", "hca", problem="Blood sample taken",
                        history="Venepuncture for annual bloods: U&E, LFT, lipids, HbA1c, TFT.")


def t_dizzy(d):
    return Consultation(d, "14:10", "Surgery Consultation", "patel", problem="Dizziness",
                        history="Room spinning on turning in bed for 1 week. No hearing loss.",
                        exam="Dix-Hallpike positive R. Neuro normal. BP lying/standing no drop.",
                        plan="Epley manoeuvre done. Advice. Return if persists.")


def t_chest_pain(d):
    return Consultation(d, "11:50", "Surgery Consultation", "patel", problem="Chest pain",
                        history="Intermittent central chest tightness on exertion 3 weeks.",
                        exam="HS normal, chest clear. BP 144/88. ECG sinus rhythm.",
                        plan="Refer rapid access chest pain clinic.",
                        values=[("Blood pressure", "144/88 mmHg")],
                        referral="Rapid access chest pain clinic, Northam General Hospital")


def t_ankle(d):
    return Consultation(d, "09:30", "Surgery Consultation", "okafor", problem="Ankle sprain",
                        history="Seen ED 3 days ago, sprained L ankle. Still sore.",
                        exam="Bruising lateral malleolus, weight bearing.", plan="RICE, physio advice.")


def t_insomnia(d):
    return Consultation(d, "17:20", "Video consultation", "hughes", problem="Insomnia",
                        history="Difficulty getting to sleep, worse with hot flushes.",
                        plan="Sleep hygiene, CBT-I app suggested.")


TEMPLATES = [(t_bp_review, 6), (t_thyroid, 4), (t_urti, 8), (t_back, 2), (t_mood, 2), (t_uti, 3),
             (t_skin, 2), (t_flu, 7), (t_med_review, 6), (t_bloods, 6), (t_dizzy, 1), (t_insomnia, 1)]


def routine_results(d: date, lab_seq: int) -> Result:
    ref = d + timedelta(days=2)
    kind = R.choice(["annual", "annual", "tft", "lipids", "fbc_normal", "fbc_normal", "msu"])
    if kind == "annual":
        lines = [ResultLine("Sodium", str(R.randint(137, 143)), "mmol/L", "133 - 146"),
                 ResultLine("Potassium", f"{R.uniform(3.8, 4.8):.1f}", "mmol/L", "3.5 - 5.3"),
                 ResultLine("Urea", f"{R.uniform(3.4, 6.8):.1f}", "mmol/L", "2.5 - 7.8"),
                 ResultLine("Creatinine", str(R.randint(58, 76)), "umol/L", "45 - 84"),
                 ResultLine("eGFR", ">90", "mL/min", ">90"),
                 ResultLine("Bilirubin", str(R.randint(5, 14)), "umol/L", "< 21"),
                 ResultLine("ALT", str(R.randint(12, 31)), "U/L", "< 33"),
                 ResultLine("Alkaline phosphatase", str(R.randint(55, 110)), "U/L", "30 - 130"),
                 ResultLine("Albumin", str(R.randint(39, 46)), "g/L", "35 - 50"),
                 ResultLine("HbA1c", str(R.randint(34, 40)), "mmol/mol", "20 - 41")]
        panel = "U&E / LFT / HbA1c"
    elif kind == "tft":
        lines = [ResultLine("TSH", f"{R.uniform(0.8, 3.2):.1f}", "mU/L", "0.27 - 4.2"),
                 ResultLine("Free T4", f"{R.uniform(13, 19):.1f}", "pmol/L", "12 - 22")]
        panel = "Thyroid function"
    elif kind == "lipids":
        lines = [ResultLine("Cholesterol", f"{R.uniform(4.6, 5.8):.1f}", "mmol/L", "< 5.0", "H" if R.random() < .4 else ""),
                 ResultLine("HDL cholesterol", f"{R.uniform(1.3, 1.8):.1f}", "mmol/L", "> 1.2"),
                 ResultLine("LDL cholesterol", f"{R.uniform(2.6, 3.6):.1f}", "mmol/L", "< 3.0"),
                 ResultLine("Non-HDL cholesterol", f"{R.uniform(3.1, 4.2):.1f}", "mmol/L", "< 4.0"),
                 ResultLine("Triglyceride", f"{R.uniform(0.8, 1.6):.1f}", "mmol/L", "< 1.7"),
                 ResultLine("Cholesterol/HDL ratio", f"{R.uniform(2.8, 4.1):.1f}", "", "< 4.5")]
        panel = "Lipid profile"
    elif kind == "fbc_normal":
        lines = [ResultLine("Haemoglobin", str(R.randint(126, 138)), "g/L", "115 - 165"),
                 ResultLine("White cell count", f"{R.uniform(4.5, 8.5):.1f}", "10*9/L", "4.0 - 11.0"),
                 ResultLine("Platelets", str(R.randint(210, 320)), "10*9/L", "150 - 400"),
                 ResultLine("Red cell count", f"{R.uniform(4.1, 4.8):.2f}", "10*12/L", "3.8 - 5.8"),
                 ResultLine("Haematocrit", f"{R.uniform(0.38, 0.43):.2f}", "L/L", "0.37 - 0.47"),
                 ResultLine("MCV", str(R.randint(84, 92)), "fL", "80 - 100"),
                 ResultLine("MCH", f"{R.uniform(28, 31):.1f}", "pg", "27 - 32"),
                 ResultLine("Neutrophils", f"{R.uniform(2.4, 5.6):.1f}", "10*9/L", "2.0 - 7.5"),
                 ResultLine("Lymphocytes", f"{R.uniform(1.2, 2.8):.1f}", "10*9/L", "1.0 - 4.0")]
        panel = "Full blood count"
    else:
        lines = [ResultLine("Urine culture", "Mixed growth", "", ""),
                 ResultLine("White cells", "<10", "/uL", "<10"), ResultLine("Red cells", "<10", "/uL", "<10")]
        panel = "Mid-stream urine"
    return Result(d, ref, ref + timedelta(days=R.randint(0, 3)), R.choice(["patel", "okafor"]), panel, lines,
                  R.choice(["Normal - no action", "Satisfactory", "Noted", "No further action"]),
                  f"NP{str(d.year)[2:]}-{lab_seq:06d}")


def brookvale_background():
    """Consultations and results from re-registration (Sep 2016) to Jan 2024, plus late 2024 care."""
    consults, results = [], []
    seq = 100000
    weights = [w for _, w in TEMPLATES]
    dates = []
    d = date(2003, 9, 15)
    while d < date(2014, 9, 20):          # first registration at Brookvale
        dates.append(d)
        d += timedelta(days=R.randint(18, 45))
    d = date(2016, 9, 20)
    while d < date(2024, 1, 31):          # re-registered after Castle Green
        dates.append(d)
        d += timedelta(days=R.randint(8, 20))
    for d in dates:
        tmpl = R.choices([t for t, _ in TEMPLATES], weights=weights)[0]
        while tmpl is t_flu and d.month not in (9, 10, 11, 12):     # flu jabs only in the autumn
            tmpl = R.choices([t for t, _ in TEMPLATES], weights=weights)[0]
        c = tmpl(d)
        if c.d.weekday() >= 5:
            c.d = c.d + timedelta(days=7 - c.d.weekday())
        consults.append(c)
        if tmpl in (t_bloods, t_thyroid) or R.random() < 0.3:
            seq += R.randint(500, 4000)
            results.append(routine_results(c.d, seq))
    # one-off episodes on fixed dates
    consults += [t_chest_pain(date(2018, 4, 9)), t_knee(date(2021, 2, 8)), t_mood(date(2020, 6, 15)),
                 t_menopause(date(2019, 10, 7)), t_ankle(date(2022, 7, 18)), t_smear(date(2017, 3, 6)),
                 t_smear(date(2020, 3, 9)), t_smear(date(2023, 3, 13)), t_back(date(2023, 9, 25)),
                 t_skin(date(2022, 11, 14))]
    # December 2023 bowel screening kit not returned (no action taken)
    consults.append(Consultation(date(2023, 12, 4), "12:00", "Administration", "admin", problem="Bowel cancer screening",
                                 history="Letter received: bowel cancer screening kit not returned. Filed."))
    consults.append(Consultation(date(2024, 4, 25), "13:40", "Administration", "okafor", problem="Advice and guidance request",
                                 history="Advice and Guidance request sent to haematology re microcytic anaemia "
                                         "Hb 112 ferritin 9. Awaiting response."))
    consults.append(Consultation(date(2024, 5, 28), "12:15", "Administration", "patel",
                                 problem="Haematology advice received",
                                 history="Haematology A&G response received 22/05. Suggests FIT and GI investigation. "
                                         "Noted, will discuss at next review."))
    # after diagnosis
    consults += [
        Consultation(date(2024, 9, 16), "11:20", "Telephone call", "okafor", problem="Adenocarcinoma of sigmoid colon",
                     history="Pt called after colorectal clinic. Diagnosis confirmed. Very upset. Husband with her. "
                             "Support offered.", plan="Macmillan contact details. Review 2 weeks."),
        Consultation(date(2024, 10, 30), "10:00", "Home visit", "patel", problem="Post-operative review",
                     history="Day 9 post anterior resection. Wound healing. Eating small amounts.",
                     exam="Wound clean, no erythema. Abdo soft.", plan="District nurse for wound check."),
        Consultation(date(2024, 12, 9), "15:00", "Surgery Consultation", "okafor", problem="Adenocarcinoma of sigmoid colon",
                     history="On adjuvant chemotherapy (CAPOX). Tired, some nausea. Mood low at times.",
                     plan="Antiemetic. Fit note 8 weeks. Review after cycle 3.",
                     meds=[Med("Ondansetron 4mg tablets", "One tablet twice a day when required", "20 tablet")]),
    ]
    results.append(Result(date(2024, 8, 20), date(2024, 8, 27), date(2024, 8, 27), "okafor", "Faecal immunochemical test",
                          [ResultLine("Faecal haemoglobin", "186", "ug Hb/g", "< 10", "H")],
                          "FIT positive - 2WW already sent 20/8", "NP24-266410"))
    consults += admin_entries()
    consults.sort(key=lambda c: (c.d, c.time))
    results.sort(key=lambda r: r.reported)
    return consults, results


ADMIN = [
    ("Medication requested", "Repeat medication requested via NHS App: amlodipine, levothyroxine. Issued.", "admin"),
    ("Telephone message", "Patient called to ask for blood test results. Advised normal per GP comment.", "admin"),
    ("Recall", "Annual hypertension review recall letter sent.", "admin"),
    ("Letter received", "Hospital appointment letter scanned and filed.", "admin"),
    ("Medication review due", "Repeat template reviewed, review date extended 12 months.", "pharm"),
    ("Prescription collected", "Electronic prescription sent to Mill Road Pharmacy.", "admin"),
    ("Test request", "Blood form printed for annual bloods.", "hca"),
    ("Registration details amended", "Mobile number updated.", "admin"),
]

ECONSULT = [
    ("Online consultation - general advice",
     "What would you like help with? Feeling more tired than usual for a few weeks. How long? 2-4 weeks. "
     "Any weight loss? Not sure. Any bleeding? No. Anything else? Busy at work, sleeping badly. "
     "Preferred contact: telephone after 4pm."),
    ("Online consultation - skin",
     "Rash on both hands, itchy, started after changing washing up liquid. No fever. Tried moisturiser. Photos attached."),
    ("Online consultation - medication query",
     "Can I take ibuprofen with my blood pressure tablets? Knee hurting after walking."),
    ("Online consultation - fit note",
     "Request for fit note extension. Still recovering from ankle sprain. Desk job, cannot drive yet."),
    ("Online consultation - menopause",
     "Hot flushes keeping me awake most nights. Would like to discuss HRT. No bleeding since last year."),
]


def admin_entries():
    out = []
    d = date(2016, 10, 3)
    while d < date(2024, 12, 15):
        if d.weekday() < 5:
            prob, hist, who = R.choice(ADMIN)
            out.append(Consultation(d, f"{R.randint(8, 17):02d}:{R.choice(['05', '20', '35', '50'])}", "Administration", who,
                                    problem=prob, history=hist))
        if R.random() < 0.09:
            prob, text = R.choice(ECONSULT)
            out.append(Consultation(d, "07:12", "Administration", "admin", problem=prob, history=text,
                                    plan="Triaged to GP telephone call."))
        d += timedelta(days=R.randint(5, 11))
    d = date(2003, 10, 1)
    while d < date(2014, 9, 1):
        prob, hist, who = R.choice(ADMIN[:5])
        out.append(Consultation(d, "11:00", "Administration", who, problem=prob, history=hist.replace("via NHS App", "by phone")))
        d += timedelta(days=R.randint(25, 60))
    return out


def castle_green_entries():
    """Castle Green Practice (SystmOne), Oct 2014 to Aug 2016, after moving to Eastmere for work."""
    out = []
    d = date(2014, 10, 14)
    whoes = ["cg_lee", "cg_ward", "cg_nurse"]
    texts = [
        ("New patient health check", "New registration. Moved for work. PMH hypertension, hypothyroid.", "BP 134/82. BMI 25.1.",
         "Continue repeats. GP2GP requested."),
        ("Hypertension review", "BP review. On amlodipine 5mg.", "BP 138/86.", "Continue."),
        ("Acute sinusitis", "Facial pain and green discharge 12 days.", "Tender maxillary sinuses. T 37.6.",
         "Phenoxymethylpenicillin 7 days."),
        ("Hypothyroidism", "TFT normal. Well.", None, "Continue levothyroxine 75mcg."),
        ("Plantar fasciitis", "Heel pain R mornings 6 weeks.", "Tender medial calcaneal tubercle.", "Stretches, insoles."),
        ("Influenza vaccination", "Flu vaccine given.", None, None),
        ("Cough", "Dry cough 3 weeks after cold. Non smoker.", "Chest clear. Sats 99%.", "Reassure. CXR if >6 weeks."),
        ("Medication review", "Annual med review.", None, "Repeats re-authorised."),
        ("Headache", "Tension type headaches with work stress.", "Fundi normal. BP 136/84.", "Advice, analgesia limits."),
    ]
    i = 0
    while d < date(2016, 8, 20):
        prob, hist, exam, plan = texts[i % len(texts)]
        who = "cg_nurse" if prob in ("Influenza vaccination", "Hypertension review") else R.choice(whoes[:2])
        meds = [Med("Phenoxymethylpenicillin 250mg tablets", "Two tablets four times a day for 7 days", "56 tablet")] \
            if prob == "Acute sinusitis" else []
        out.append(Consultation(d, R.choice(["09:00", "10:40", "14:20", "16:30"]),
                                R.choice(["Surgery Consultation", "Surgery Consultation", "Telephone call"]),
                                who, problem=prob, history=hist, exam=exam, plan=plan, meds=meds))
        d += timedelta(days=R.randint(6, 14))
        i += 1
    return out


def lloyd_george_cards():
    """(entries, messy) per card: 1986 to 2003, paper era at the previous practice."""
    pool = [
        ["c/o sore throat 3/7. T 37.8", "tonsils inflamed, no exudate", "Pen V 250 qds 10/7"],
        ["BP check 142/88", "rpt 3/12. lifestyle advice"],
        ["L knee pain after fall", "no effusion, full ROM", "analgesia, review prn"],
        ["Pill check. BP 118/74", "Microgynon cont. 6/12"],
        ["tired ++. ?anaemia", "FBC, TFT"],
        ["FBC nad. TFT TSH 8.9", "start thyroxine 50mcg", "rpt TFT 6/52"],
        ["URTI. chest clear", "fluids, paracetamol"],
        ["smear taken", "no problems"],
        ["low back pain, lifting", "no sciatica. NSAID, advice"],
        ["mole L arm, regular", "edges even. reassure"],
        ["thyroxine incr to 75mcg", "TSH 5.4"],
        ["gastroenteritis 2/7", "fluids. off work 3/7"],
        ["BP 148/92, 152/94", "start amlodipine 5mg?", "recheck 2/52"],
        ["BP 136/84 on amlodipine", "cont. rpt 6/12"],
        ["dysuria 2/7. dip nit+ leu+", "trimethoprim 200 bd 3/7"],
        ["wants to stop pill", "advised condoms. leaflet"],
        ["R wrist pain, typing", "?tenosynovitis. splint, rest"],
        ["chickenpox contact - son", "had as child. reassure"],
        ["tetanus booster given", "L deltoid"],
        ["headaches, tension type", "BP 128/80. fundi nad", "simple analgesia"],
        ["cough 3/52 non smoker", "chest clear. reassure"],
        ["rash both shins - ?eczema", "emollient + HC 1%"],
        ["flu jab", "no reaction"],
        ["heavy periods again", "Hb 121. tranexamic acid"],
        ["moving away - wants records", "GP2GP n/a. card sent"],
    ]
    cards, d = [], date(1986, 5, 12)
    idx = 0
    for card_no in range(14):
        entries = []
        for _ in range(R.randint(5, 7)):
            if d > date(2003, 8, 1):          # paper era ends when computerised at Brookvale
                break
            entries.append((d, pool[idx % len(pool)]))
            idx += 1
            d += timedelta(days=R.randint(45, 160))
        if not entries:
            break
        messy = 0.85 if card_no in (3, 7, 11) else R.choice([0.35, 0.45, 0.55, 0.6])
        cards.append((entries, messy))
    return cards
