# 0022 The Help form emails the expert's free text to our support address

Date: 2026-10-25 · Status: accepted · Decided by: the founder

**Problem.** Experts who are stuck want to write to us from the Help page. Email in Avodeck is "templates with checked parameters only" (`core/email.py`), so nothing the expert types could be sent.

**Decision.** `POST /v1/help-requests` (`{topic, message, page}`) sends one `help.request` email to our support address, hello@avodeck.com (fixed in `app.core.config.SUPPORT_EMAIL`, not a setting) carrying the full free text. This is the one exception to the checked-parameters rule: the `help.request` template is allowed a `message` of 1 to 4000 characters (no control characters other than newline and tab). Reply-to is the expert.

**Risk.** The expert may type patient details despite the warning. The text then passes through the email provider and lands in our inbox.

**Safeguards.**
- The form says "Please don't include patient names or details."
- The text goes only into the email: never stored in the database, logs, the audit log or Sentry (request bodies are never sent). The audit row `help.request_sent` carries no text.
- The only recipient is our own fixed address; it is never sent to the browser. The form is always shown.
- Signed-in people only, CSRF as for every POST, at most 5 a day per person (429).
- Other templates keep their checked parameters; the guard `api/tests/guards/test_foundation_email_free_text.py` allows free text in `help.request` only.
- Inbox handling: support deletes messages that contain patient details and tells the expert.
