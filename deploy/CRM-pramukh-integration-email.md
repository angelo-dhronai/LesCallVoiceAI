# Email to Pramukh CRM vendor — Push API integration

Ready to send. Replace the bracketed bits before sending.

---

**Subject:** Pramukh Push API — our field list, lead types & project names + 6 open points

Hi [Name],

Thanks for the Push API document. We've reviewed it and we're ready to build against
it. Below is everything you asked us to share, plus a few points we need from your
side before we can go live.

For context on what's sending the data: LesCall is an AI voice agent that answers
Pramukh's inbound calls and places outbound/follow-up calls. It talks to the caller in
English, Hindi or Gujarati, captures their requirement, and books site visits. Every
completed call produces a lead record, which is what we'll push to you.

---

## 1. The `type` list you asked for

These are the values we will send in `type`. All of them are voice-call sources, so
they won't collide with your existing web-form and portal sources:

| `type` value | Meaning |
|---|---|
| `LesCall_Inbound_Call` | Caller dialled the Pramukh number; our AI agent answered. |
| `LesCall_Outbound_Call` | Our AI agent called the prospect from a Pramukh campaign list. |
| `LesCall_Followup_Call` | Our AI agent called back on a commitment made in an earlier call. |

**If you'd rather keep it to one value**, tell us and we'll send `LesCall_Voice_AI`
for everything. Equally, if you want these split further (by campaign, by agent, by
outcome), we can — just send us the exact strings you want and we'll emit those
verbatim. Please confirm these need to exist in your master list before we start
pushing, so we don't trip error `8865`.

## 2. The `project_name` list you asked for

These are the projects our agent knows and can attribute a lead to:

| Project | City |
|---|---|
| Revanta | Surat |
| Vivanta | Surat |
| Satva | Surat |
| Agastya | Surat |
| Central Park | Surat |
| Aranya | Surat |
| Aranya II | Surat |
| Orbit | Surat |
| Oak Cove | Surat |
| Aura | Vapi |
| Sahaj | Vapi |
| Vedanta | Vapi |
| Sangath | Silvassa |
| Yogi Woods | Silvassa |
| Shivanta | Silvassa |
| Swagat | Silvassa |

**Two things we need back on this:**

- **Naming convention.** Do you want the bare name (`Aura`, `Revanta`,
  `Vivanta`) or the prefixed form (`Pramukh Aura`, `Pramukh Revanta`,
  `Pramukh Vivanta Residences`)? We have both in our system and will standardise on
  whichever your CRM matches on. Please send the exact strings from your master
  table — an exact-match list is safest for both of us.
- **Empty project.** Many callers describe a requirement (city, BHK, budget) without
  naming a project. Is `project_name` allowed to be empty, or should we send a
  placeholder such as `General Enquiry`? We'd rather not guess a project and
  mis-attribute the lead.

## 3. The fields we will send on every push

Exactly the nine parameters in your document, populated as follows:

| Parameter | What we send | Notes |
|---|---|---|
| `api_key` | `ZMASsa5GzPn4lk7GvzB9rKsZSAZsdf3Q2` | As issued. |
| `signature` | `Pr@mUkh2k5WWal10825` | As issued. |
| `type` | One value from §1 | |
| `name` | Caller's name as captured in conversation | Empty if the caller declined to give it. |
| `email` | Caller's email | **Usually empty — see §5.** |
| `countrycode` | `91` | Digits only, no `+`. |
| `contactno` | 10-digit national number | Normalised, no spaces or punctuation. |
| `project_name` | One value from §2 | Empty when no project was named — see §2. |
| `remarks` | Composed call summary | Format below. |

### `remarks` format

Your API has no dedicated fields for call data, so everything about the call goes into
`remarks` as a structured block. Sample:

```
Call: Inbound | 31-Jul-2026 14:22 IST | 3m 12s | Language: Gujarati
Interest: 3 BHK, Vesu Surat, budget ₹1.1–1.3 Cr, purchase in ~3 months, home loan required
Outcome: Site visit booked — Sat 02-Aug, 4:00 PM
Agent: Vishal Patel (LesCall AI)
Recording: https://agent.lescall.com/api/recordings/dl/<token>
Summary: Caller is relocating from Mumbai, wants possession before the school year.
Compared Revanta and Vivanta; preferred Revanta on budget. Confirmed for a Saturday visit.
```

The recording link is a single-use-style tokenised URL — it opens the call audio
directly in a browser, no login needed on your side.

**We need to know your `remarks` column limit** (character count) so we can truncate
cleanly rather than have you reject the record. Also please confirm the column accepts
**UTF-8 / Unicode**, since summaries can contain Gujarati and Hindi text and the ₹
symbol.

## 4. Data we already hold and can send the moment you add fields for it

Packing a whole call into `remarks` is workable but it means none of this is queryable
or reportable inside your CRM. If you can add optional parameters, we can populate all
of the following today — no extra work on the Pramukh side beyond accepting them:

**Call metadata**

| Suggested parameter | Example |
|---|---|
| `call_direction` | `inbound` / `outbound` |
| `call_datetime` | `2026-07-31 14:22:05` (IST) |
| `call_duration_seconds` | `192` |
| `call_language` | `Gujarati` |
| `recording_url` | `https://agent.lescall.com/api/recordings/dl/<token>` |
| `transcript_url` | Full turn-by-turn transcript link (we can expose the same tokenised style) |
| `call_reference_id` | Our unique call ID, for reconciliation between systems |

**Requirement captured in conversation**

| Suggested parameter | Example |
|---|---|
| `location_preference` | `Vesu, Surat` |
| `configuration` | `3 BHK` |
| `property_type` | `Apartment` / `Villa` / `Plot` |
| `budget` | `₹1.1 Cr – ₹1.3 Cr` |
| `purpose` | `Self-use` / `Investment` |
| `purchase_timeline` | `3 months` |
| `loan_required` | `Yes` / `No` |
| `whatsapp_number` | `9988774455` |

**Outcome and qualification**

| Suggested parameter | Example |
|---|---|
| `call_outcome` | `Site visit booked` / `Callback requested` / `Not interested` |
| `site_visit_datetime` | `2026-08-02 16:00` |
| `followup_datetime` | `2026-08-05 11:00` |
| `lead_score` | `0–100` |
| `sentiment` | `positive` / `neutral` / `negative` |
| `customer_intent` | Short phrase, e.g. `Ready buyer, comparing two projects` |
| `objections` | `Price above budget; wants possession sooner` |
| `call_summary` | 2–3 line summary as its own field rather than inside remarks |

We're not asking you to take all of these. Even just `recording_url`,
`call_datetime`, `call_duration_seconds` and `call_outcome` as real columns would make
the data far more useful to the Pramukh sales team than a text blob.

## 5. Open points we need answered before go-live

1. **The PUSH URL.** Your document says it will be shared after confirmation — please
   send it. A **staging/test endpoint** as well, if you have one, so we can verify the
   full flow without creating junk records in production.

2. **Is `email` mandatory?** This is our biggest concern. These are *voice* calls —
   callers give a name and phone number readily, but rarely an email address. We
   expect `email` to be empty on the majority of pushes. Please confirm: does an
   empty `email` pass, or does it return `8865 Invalid Input values`? If it's
   mandatory, tell us what placeholder you want (e.g. `noreply@lescall.com`) — though
   we'd strongly prefer it be optional, since a fake email in your CRM is worse than
   no email.

3. **Repeat calls to the same number.** Your API returns
   `1404 Duplicate Record Found` for a contact that already exists. But a prospect who
   calls three times generates three calls with different content — the second and
   third are often the *more* valuable ones (a booking, a change of requirement). How
   should we send those?
   - Is there an **update/append endpoint** we should call instead?
   - Or should each call be pushed as a new record, with duplicates handled on your
     side?
   - Or do you want us to push only the **first** call per contact and hold the rest?

   Right now we'll treat `1404` as "delivered, already known" rather than an error, so
   nothing breaks either way — but the follow-up call detail will be lost to you until
   this is settled. This matters more than it sounds.

4. **Duplicate window.** Related to the above — is the duplicate check on phone number
   alone, or phone + project, or phone + type? That determines whether the same
   prospect enquiring about two different projects lands as one record or two.

5. **Rate limits and IP allowlisting.** Do you enforce a request-per-minute limit? And
   do you need our server's egress IP allowlisted? It is **35.207.225.237**.

6. **Credential handling.** The API key and signature arrived in a document over
   email. We'll store them encrypted and scoped to the Pramukh account only. If you'd
   prefer to rotate them now that they've been through email, we're happy to
   re-key — just send the replacements through a channel of your choosing.

## 6. What happens on our side

Once we have the PUSH URL and your answers to §5, the integration is enabled **only
for the Pramukh account** on our platform — every other client account on LesCall is
unaffected and pushes nothing. Delivery is queued and retried with backoff, so a
temporary outage on either side never loses a lead; anything that ultimately fails is
visible to us and can be replayed.

One note on direction: your document covers the push (leads into your CRM) only. If
you also have a **read/fetch API** — an endpoint that lists contacts or leads out of
the CRM — please send that spec too. Being able to pull the Pramukh contact list into
LesCall would let the AI agent make outbound and follow-up calls directly against your
live CRM data instead of an uploaded file. Not a blocker for this phase, but it's the
natural next step and worth scoping now.

Happy to get on a short call if that's faster than email for any of the above.

Best regards,
[Your name]
[Your title] · LesCall
[Phone] · [Email]

---

## Internal notes — not part of the email

- **Project list source:** `calling-agent/agents/pramukh/agent.yaml`
  (`knowledge_base.known_projects`, 16 projects across Surat / Vapi / Silvassa).
  A second, older definition at `calling-agent/agents/pramukh_agent/agent.yaml` uses
  the prefixed names (`Pramukh Aura`, `Pramukh Revanta`,
  `Pramukh Vivanta Residences`) and only 3 projects — hence the naming-convention
  question in §2. Whichever they pick, both agent definitions must be standardised to
  it before go-live, or leads will attribute inconsistently.
- **`email` will almost always be empty:** the Pramukh agent's slot set is
  name / location / bhk / property_type / budget / purpose / timeline / loan_required.
  There is no email slot. Q2 in §5 is therefore a genuine blocker, not a formality.
- **Recording link** is `PUBLIC_BASE_URL` + `/api/recordings/dl/<download_token>`
  (`shared/recording_storage.py:80`), served by the auth-exempt route at
  `dashboard/server.py:2051`. The token is the credential — 256-bit random.
- **Transcript link does not exist yet.** §4 offers `transcript_url` as something we
  *can* expose in the same tokenised style; it needs building if they take us up on it.
- **Everything in §4 is real data we already store:** `calls` +
  `call_intelligence` (`shared/db/migrations/0001_intel_core.sql`) + slot capture +
  `bookings` + `follow_ups`.
