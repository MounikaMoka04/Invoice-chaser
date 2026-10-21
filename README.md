# Invoice Chaser

> **Riya is a freelance designer.** She delivers a ₹40,000 brand kit, the client says "I'll pay by Friday,"
> and three Fridays pass in silence. Riya hates sending the reminder because it feels rude, so she waits,
> and the longer she waits, the less likely she is to be paid.
>
> **Invoice Chaser sends that reminder for her.** It tracks every unpaid invoice, writes a polite WhatsApp
> message at the right moment, gets a little firmer each time, understands the client's reply, and stops the
> moment the money arrives.

This is a complete, working Python full-stack project: a FastAPI backend, PostgreSQL (or SQLite for quick
starts), a LangGraph agent, an LLM (Gemini or Groq) that writes the wording, the WhatsApp Cloud API for
delivery, a daily scheduler, and a simple web dashboard. It runs with **no API keys** in a safe demo mode
(template wording, and WhatsApp messages printed to the log instead of sent), so students can start immediately.

---

## 1. Quick start (2 minutes, no keys needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m scripts.seed                                  # optional: demo account + 5 invoices
uvicorn app.main:app --reload
```

Open **http://localhost:8000**, log in with `demo@example.com` / `demo1234`, and click **Run follow-ups now**.
Watch the terminal: each `[MOCK WHATSAPP]` line is a message that would have been sent. The interactive API
documentation is at **http://localhost:8000/docs**.

### With Docker and PostgreSQL

```bash
cp .env.example .env
docker compose up --build
docker compose exec app python -m scripts.seed
```

### Run the tests

```bash
pytest          # 54 tests, about 5 seconds, no network needed
```

---

## 2. How it works

### The idea in one sentence
**Python rules decide *when* and *whether* to message; the LLM only writes and reads the words.**
That split keeps the system predictable, testable and safe: a model can never decide to message someone at
midnight, invent an amount, or send a tenth reminder.

### The follow-up flow (a LangGraph graph)

```
 load ─► check_limits ─► draft ─► approval_gate ─► send ─► END
  │          │
  └─ stop    └─ stop   (any node can end the run early)
```

| Node | What it does | Ends the run when |
| --- | --- | --- |
| `load` | Fetches the invoice and client | invoice is paid, paused or disputed; client opted out; invoice needs review; a draft already awaits approval |
| `check_limits` | Applies the guardrails | reminder cap reached; next reminder not due yet; quiet hours (9 pm to 9 am IST); client already got 3 reminders this week |
| `draft` | Chooses the tone for this reminder and asks the LLM for the message | never (falls back to a template if the LLM fails validation) |
| `approval_gate` | In approval mode, saves the draft for the owner instead of sending | the owner has approval mode switched on |
| `send` | Sends on WhatsApp, records the message, schedules the next reminder | WhatsApp rejects the send (retried on the next run) |

### The tone ladder

| Reminder | Sent on | Tone | Purpose |
| --- | --- | --- | --- |
| 1 | the due date | friendly | A gentle heads-up with the payment link |
| 2 | due + 3 days | polite | Ask the client to confirm a payment date |
| 3 | due + 7 days | firm | State the overdue amount, ask for a firm date |
| 4 | due + 14 days | formal | Note it is well overdue, offer to resolve any issue |
| 5 | due + 21 days | final | Last automatic notice; the owner is alerted to follow up personally |

A client marked **friendly** gets softer wording one step earlier; a client marked **formal** always gets at
least a formal tone. If an invoice is entered late, reminders are spaced at least `MIN_GAP_DAYS` apart, so a
client is never messaged every day. All of this lives in `app/services/tones.py`.

### Reading replies (a second LangGraph graph)

```
 classify ─► paid | more_time | dispute | stop | other ─► END
```

| Client says | The system does |
| --- | --- |
| "I have paid" | Pauses reminders, flags the invoice for **you to verify** (it never trusts a claim blindly), thanks the client |
| "Need 5 more days" | Moves the next reminder out by 3 days and acknowledges |
| "This amount is wrong" | Marks the invoice disputed, stops reminders, alerts you |
| "STOP" | Opts the client out and pauses all their open invoices |
| anything else | Flags it for you to answer; no automatic reply |

Replies are classified by clear keyword rules first and only then by the LLM, so "STOP" can never be missed
because a model had a bad day (`app/services/llm.py`).

### What the LLM is allowed to do

* Write one short WhatsApp message from structured facts (invoice number, amount, due date, link, tone).
* Classify an ambiguous reply into one of six intents.

Every draft is **validated** before sending: it must mention the invoice number and the right amount, must not
contain threats ("legal action", "police" ...), must not contain any link except the payment link, and must be
under 700 characters. If validation fails, or the API errors, or no key is set, the fixed template for that
stage is used instead.

### WhatsApp rules the code respects

WhatsApp only allows free-form text within 24 hours after the customer last wrote to you. Outside that window a
business must use a **pre-approved template**. `app/services/delivery.py` checks the window automatically:
inside it, the LLM text is sent; outside it, the approved template is sent with these five placeholders in order:

`{{1}}` client name, `{{2}}` invoice number, `{{3}}` amount, `{{4}}` due date, `{{5}}` payment link

Example template body to submit for approval (category **Utility**):

```
Hi {{1}}, a reminder that invoice {{2}} for {{3}} is due on {{4}}. You can pay here: {{5}}. Thank you!
```

---

## 3. Configuration

Copy `.env.example` to `.env`. Everything has a default, so only change what you need.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | SQLite file | Use `postgresql+psycopg://user:pass@host:5432/db` for PostgreSQL |
| `SECRET_KEY` | dev value | **Change in production.** Signs login tokens |
| `LLM_PROVIDER` | `template` | `template`, `gemini` or `groq` |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | empty | Get a free key from Google AI Studio or Groq |
| `GEMINI_MODEL`, `GROQ_MODEL` | see file | Model names change over time; update them from the provider's docs if a call fails |
| `WHATSAPP_MODE` | `mock` | `mock` logs only; `cloud` really sends via Meta's Cloud API |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | empty | From your Meta app's WhatsApp setup page |
| `WHATSAPP_VERIFY_TOKEN` | dev value | The secret you type when registering the webhook |
| `WHATSAPP_APP_SECRET` | empty | If set, webhook signatures are verified (recommended in production) |
| `MAX_REMINDERS`, `MIN_GAP_DAYS` | 5, 2 | Reminder cap per invoice, minimum days between reminders |
| `QUIET_START_HOUR`, `QUIET_END_HOUR` | 21, 9 | No messages between these hours (local time) |
| `MAX_MESSAGES_PER_CLIENT_PER_WEEK` | 3 | Weekly cap per client across all invoices |
| `REQUIRE_APPROVAL` | false | Hold every reminder for the owner's approval (each user can also switch this on in the dashboard) |
| `SCHEDULER_HOURS` | `10,16` | Hours of the day when the follow-up cycle runs |

### Going live with WhatsApp (checklist)

1. Create a Meta developer app and add the **WhatsApp** product; note the temporary token and phone number ID.
2. Submit the reminder template above for approval (it can take a while, so do this first).
3. Expose your server with HTTPS (for development use a tunnel such as ngrok) and register
   `https://your-host/webhooks/whatsapp` as the webhook URL with your `WHATSAPP_VERIFY_TOKEN`; subscribe to `messages`.
4. Set `WHATSAPP_MODE=cloud`, the token and phone number ID, and restart.
5. Test with your own number first. Only message clients who expect to hear from you.

---

## 4. API summary

Full interactive docs: `/docs`. All endpoints except register, login and the webhook need
`Authorization: Bearer <token>`.

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/register`, `POST /auth/login`, `GET/PATCH /auth/me` | Account, login, approval-mode switch |
| `GET/POST /clients`, `PATCH/DELETE /clients/{id}` | Manage clients (delete removes all their data) |
| `GET/POST /invoices`, `GET/PATCH /invoices/{id}` | Manage invoices; PATCH `action`: `mark_paid`, `pause`, `resume`, `dispute`, `clear_review` |
| `POST /invoices/{id}/remind` | Send the next reminder now (still obeys caps and quiet hours) |
| `POST /invoices/import` | Bulk CSV import |
| `POST /agent/run` | Run today's follow-up cycle for your invoices |
| `GET /messages/pending`, `POST /messages/{id}/approve`, `POST /messages/{id}/reject` | Approval mode |
| `GET /dashboard/summary` | Outstanding, overdue, recovered ₹, recovery rate, days to pay, upcoming, needs attention |
| `GET/POST /webhooks/whatsapp` | Webhook verification, replies and delivery receipts |

CSV columns: `client_name, whatsapp_number, invoice_number, amount, issue_date, due_date, payment_link, description`
(dates as `YYYY-MM-DD`; `payment_link` and `description` may be empty).

### Dashboard numbers

* **Recovered ₹** (the headline metric): value of invoices marked paid after at least one reminder was sent.
* **Recovery rate**: recovered ₹ divided by (recovered ₹ plus ₹ still overdue).
* **Avg days to pay**: days from the first reminder to the payment date, for recovered invoices.
* **Delivery rate**: reminders sent or delivered, divided by those plus failed.

---

## 5. Project structure

```
app/
  main.py            FastAPI app, startup, serves the dashboard
  config.py          All settings (environment variables)
  database.py        SQLAlchemy engine and session
  models.py          users, clients, invoices, messages
  schemas.py         Request and response models, phone validation
  security.py        Password hashing and JWT login
  scheduler.py       APScheduler: runs the daily cycle
  agent/
    graph.py         The two LangGraph graphs (reminders, replies)
    runner.py        Daily cycle: refresh statuses, run the graph per due invoice
  services/
    tones.py         Schedule and tone ladder (pure Python rules)
    llm.py           Gemini/Groq calls, validation, templates, reply classification
    whatsapp.py      Mock and Meta Cloud API sending
    delivery.py      Send a stored message, 24h window, bookkeeping
    inbound.py       Webhook payload handling
  routers/           auth, clients, invoices, dashboard, webhooks
  static/index.html  Dashboard (plain HTML + JavaScript)
scripts/seed.py      Demo data
tests/               54 tests: API, agent, LLM guardrails, webhook, dashboard, scheduler
```

---

## 6. Suggested team split (trainers and project managers)

| Phase | Build | Check |
| --- | --- | --- |
| 1. Setup | Run the quick start, read `models.py`, draw the data model | PM confirms scope and sets review dates |
| 2. Core app | Walk through clients, invoices and the dashboard routers | PM adds 10 realistic invoices and checks each screen |
| 3. Agent | Study `tones.py` and `agent/graph.py`; change the tone ladder | PM reviews the wording and the reminder cap |
| 4. Messaging | Switch on a real LLM key and the WhatsApp sandbox | PM tracks template approval |
| 5. Hardening | Run and extend the tests, polish the demo | PM runs the demo script against the acceptance list |

**Acceptance checks** (all covered by the automated tests):
a due invoice gets exactly one reminder; the next reminder uses the next tone; marking paid stops everything;
quiet hours and caps are respected; "paid", "more time", dispute and STOP replies are handled correctly;
the dashboard shows correct ₹ figures.

## 7. Ideas to extend it

* Add a pre-due reminder (2 days before the due date) in `SCHEDULE_DAYS`.
* Support Hindi and Telugu messages by adding a `language` field on clients and passing it into the prompt.
* Let users edit the tone ladder and schedule from the dashboard.
* Send the owner a weekly summary email.
* Add Alembic migrations, rate limiting and a background worker (Celery) for large volumes.
* Add payment-link generation (Razorpay) and automatic "paid" detection from its webhook.

## 8. Known limitations (be honest with students)

* Amounts are stored as floats in rupees, which is fine for a demo; use `Decimal`/paise integers for real accounting.
* Tables are created automatically at startup; production systems should use migrations (Alembic).
* The default `SECRET_KEY` is for development only.
* LLM model names and WhatsApp API versions change; check the providers' current documentation before going live.
* This is a product prototype, not legal advice. Follow WhatsApp's messaging policy and India's data protection
  rules, and message only clients who expect to hear from you.
