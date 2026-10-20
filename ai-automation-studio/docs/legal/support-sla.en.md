# Support Terms (Service Level Agreement)

> **Template. Not legal advice.** Before the first use, a lawyer qualified in Georgia must review
> it (see `README.md`). Fill the fields in `{{double braces}}` once and the fields in
> `[square brackets]` for each client. Delete this box before you send it.

These Support Terms are part of Agreement No. [number] dated [date] between {{NAME}} (the
**Developer**) and [Client's legal name] (the **Client**). Capitalised words, such as
**Business Day**, **Client Accounts** and **Order Form**, have the meaning given in the Agreement.

They apply while the Client has an active support plan. While the Developer hosts the Client's
system, a support plan is required. The plan starts at Launch (Agreement, 7.2). Free fixes after
Launch (Agreement, 5) come on top of it.

---

## 1. Plans at a glance

| | Care | Pro | Business |
|---|---|---|---|
| **Price per month** | $49 / 99 ₾ | $149 / 249 ₾ | $349 / 590 ₾ |
| Hosting, monitoring, daily backups, monthly security updates, monthly report | ✓ | ✓ | ✓ |
| First response within | 2 Business Days | 1 Business Day | 4 business hours |
| Hours for small changes per month | 1 hour | 4 hours | 10 hours |
| Repairs after outside changes | count toward the plan's 1 hour; above it, extra hours | included, no hours used | included, no hours used |
| Fixes of the Developer's own bugs | always free | always free | always free |
| AI operations per month | 2,000 | 10,000 | 30,000 |
| One new small automation per quarter, up to 6 hours | — | — | ✓ |

The price in the Order Form applies if it differs from this table.

**Business hours:** Monday to Friday, 10:00–19:00 Tbilisi time (UTC+4), except public holidays in
Georgia.

## 2. What every plan includes

2.1. **Hosting.** The Developer runs the system on its server. Each client has a separate
deployment: its own configuration, its own data and its own process.

2.2. **Monitoring.** Each scheduled run and the running bot report to a monitoring service. If a run
fails or stops reporting, the Developer gets an alert. The Developer checks alerts every Business
Day. Monitoring shows that the system runs and that its runs succeed. It cannot judge every result.
For example, it cannot tell whether a competitor's page shows the right product. That is why every
price comes with the page text it was read from.

2.3. **Backups.** [VERIFY: backups. Keep this clause only if the automated daily backup job from
`README.md`, section 6, runs for this client. Otherwise delete it and "daily backups" in section 1.]
Every day, the system's data is copied to separate storage: bookings, price history, email
processing records and the event log. Backups are encrypted and kept for up to 30 days. Restoring
from a backup is incident work (section 4). Data in Client Accounts, such as the Google Sheet, the
mailbox or the calendar, is protected by those services' own tools, such as the version history of
Google Sheets. The Developer does not back it up.

2.4. **Security updates.** Once a month, the Developer updates the software components, runs the
automated tests, redeploys the system and checks that each client's setup works.

2.5. **Monthly report.** By the [5th] Business Day of each month, the Client gets a report for the
previous month. It shows:
- what the system did: for example, bookings taken, emails sorted, prices checked, alerts sent;
- how reliably the scheduled runs worked;
- an estimate of the hours saved, with the method shown.

The report is built only from the system's event log. Anything estimated is marked as an estimate.
The support hours used are sent with the report as a separate time log (5.1).

## 3. How to ask for support

3.1. **Channels.** [Direct clients: email {{EMAIL}} or Telegram @{{TELEGRAM}}.] Clients who hired
the Developer through a marketplace use only the marketplace's messages.

3.2. **What helps.** What happened, when, and one example: a screenshot, a booking code, or an
email's subject line. Never send a password in a chat. The Developer suggests a safe way to pass it,
such as a one-time secret link or a call.

3.3. **Response time** is the time to the Developer's first personal reply, not an auto-reply. The
reply confirms the request and says what happens next. It is counted in business hours from the
moment the request arrives, or from the monitoring alert for an incident the Developer noticed
first. A request that arrives outside business hours is counted from the start of the next Business
Day.

3.4. **Fix time.** The Developer does not promise a fixed time to fix a problem. Many causes are
outside its control: a provider's outage, a revoked password, a redesigned website. For a critical
incident, the Developer:
- works on it first, during business hours;
- updates the Client at least once each Business Day until it is fixed;
- afterwards explains what happened and what was changed so that it does not happen again.

3.5. **Critical incident** means the system has stopped doing its main job, or shows wrong
information to customers. For example: the bot does not take bookings or send reminders, emails are
not processed, prices are not checked. The Developer may work on a critical incident outside business
hours, but does not promise to.

3.6. **Absence.** The Developer tells the Client at least 2 weeks ahead about any planned absence of
more than 3 Business Days, and how incidents will be handled during it. Monitoring keeps running.

## 4. Incident or change request

4.1. Every request is one of these types:

| Type | Examples | How it is paid |
|---|---|---|
| **The Developer's bug:** the system does not work as described in the Order Form | A reminder goes out at the wrong time. A row is missing from the sheet. The bot stops after an update. | Always free. No plan hours are used. |
| **Repair after an outside change:** something outside the system changed | A competitor redesigns a product page. The Client changes the mailbox password. A provider changes its interface. Sheet access was removed. | Care: counts toward the plan's 1 hour; above it, extra hours (5.3). Pro and Business: included, no hours used. |
| **Small change** | Prices, services, staff, opening hours, holidays, FAQ, email categories, company facts, competitor links within the tier, bot texts, alert threshold. | Plan hours. |
| **New small automation** (Business only) | A weekly summary in Telegram. A new report tab. A new email category with its own action. | One per quarter, up to 6 hours. An unused one does not carry over. |
| **Custom work** | New features, integrations or channels (WhatsApp, Instagram, a CRM). New data sources. Anything above the tier's limits. | Quoted separately. |

4.2. A repair brings back the same function on the same site or service. Some outside changes need a
new approach:
- if a site starts showing prices only after JavaScript runs, reading it again is custom work;
- if a site moves its prices behind a login or bot protection, it is removed from monitoring, as set
  in the Agreement (11.4);
- if a provider closes a service, replacing it is custom work.

The Developer says so before starting.

4.3. If it is unclear whether something is an incident or a change, the Developer says which it is
before starting. The Developer's own bugs never cost the Client anything.

## 5. Hours

5.1. Plan hours are counted in 30-minute steps. The Developer keeps a time log (date, task, time) and
sends it with the monthly report, as a separate document.

5.2. Unused hours do not carry over to the next month.

5.3. Extra hours are paid at the hourly rate in the Order Form, in 30-minute steps, and only after
the Client approves the estimate.

5.4. If the Client goes over its plan's hours two months in a row, the Developer suggests the next
plan. The Client decides.

## 6. AI operations

6.1. One AI operation is one request to the AI model:
- an email sorted = 1, or 2 with a reply draft;
- a message a customer types to the bot = 1, or 2 if it is a question the bot answers;
- a competitor page read by AI = 1.

Buttons, newsletters filtered out by rules, and prices read from page markup are not counted. The
Basic booking bot uses no AI at all.

6.2. The monthly limit is enforced as a spending cap on an AI provider key used only for the
Client's system. The cap is set in money, for the plan's number of operations at the price of the
recommended model, so the exact number of operations it allows depends on their length. The
Developer does not charge for AI use above the limit.

6.3. **When the cap is reached,** the Developer tells the Client, and the AI pauses until the 1st
of the next month, or until the Client moves to a higher plan (11.1):
- the booking bot keeps taking bookings with buttons and sending reminders, and reads typed
  messages with simple rules, in English and Russian;
- new emails wait, unsorted, with no reply drafts or hot-lead alerts. They are sorted when the AI is
  available again;
- competitor pages without price markup are marked "not checked this time". Prices from page
  markup are checked as usual.

If the Client reaches the cap two months in a row, the Developer suggests the next plan. The Client
decides.

6.4. The limits apply to the AI model the Developer recommends for the package. A more powerful model
at the Client's request is priced separately in the Order Form.

6.5. If the Client uses its own AI account (Order Form), the limits do not apply. The Client pays the
provider directly.

## 7. What the Client does

- Keeps Client Accounts working: the sheet stays shared, the mailbox access stays valid, the bot is
  not deleted, the payment card in WhatsApp Manager stays valid.
- Tells the Developer before changing a password, the sheet's structure, staff or opening hours.
- Reviews reply drafts before sending them, and keeps company facts and FAQ answers up to date.
- Names one contact person for support.
- Pays third-party fees, such as WhatsApp messages and a Meta partner's fee.

## 8. Not included in any plan

- New features, integrations, channels or data sources. These are custom work.
- Work on the Client's other systems, websites, computers or accounts.
- Third-party fees: WhatsApp messages (Meta bills the Client), a Meta partner's monthly fee, the
  Client's own subscriptions.
- AI use above the plan's limit. At the limit, the AI pauses (section 6).
- Hosting on the Client's own server or on a dedicated server, unless the Order Form includes it.
- Recovering data deleted inside Client Accounts. Use the account's own history or backups.
- Outages of Third-Party Services. The Developer watches them, tells the Client, and repairs its side
  once the service is back.
- Writing the Client's business content, such as prices, policies or FAQ answers. The Developer
  enters what the Client provides.
- Legal, tax, accounting or marketing advice.
- A guaranteed uptime percentage. These terms promise monitoring and response times.

## 9. Maintenance

9.1. Updates can restart the system for a few minutes. The Developer does them outside the Client's
busiest hours where possible. For planned downtime of more than 30 minutes, the Developer tells the
Client at least 1 Business Day ahead.

9.2. Restarting the booking bot does not lose bookings or reminders. A customer who was in the middle
of a booking sees the menu again.

## 10. Payment and suspension

10.1. Support is paid monthly in advance. The invoice comes on the 1st and is due within 7 days.
Clients who hired the Developer through a marketplace pay through it. For example, on Upwork, a
weekly payment equal to the monthly price divided by 4.33: $12, $35 or $81.

10.2. If an invoice is not paid within 7 days after the due date, the Developer sends a reminder. If
it is still unpaid 7 days after the reminder, the Developer may suspend support and hosting, with at
least 3 Business Days' written notice.

10.3. While the service is suspended, the system stops. The bot stops replying and sending
reminders, emails are not sorted, prices are not checked. The Client should tell its customers how
else to book.

10.4. Suspension never blocks the Client's access to Client Accounts or to the data in them. On
request, the Developer exports the Client Data held on its server even during a suspension. The
Developer does not delete data in the first 30 days of a suspension. After payment, the service
restarts within 1 Business Day.

10.5. If the invoice is still unpaid 30 days after the suspension started, the Developer may end the
support plan. The end-of-service rules in section 11 apply. Suspension does not cancel the debt.

## 11. Changing or ending a plan

11.1. The Client may move to a higher plan at any time. The difference for the rest of the month is
charged pro rata. A move to a lower plan takes effect from the next month.

11.2. The Client may end its plan at any time in writing. The service runs to the end of the paid
month. Partial months are not refunded.

11.3. The Developer may end a plan with 30 days' written notice. It refunds any prepaid days after
the end, and the copy of the software is free (Agreement, 10.4).

11.4. When a plan ends:
- if the Client asks within 30 days, the Developer exports the Client Data held on its server in CSV
  or another common format: bookings, price history, invoice rows, reports;
- the Telegram bot, the Google Sheet and the WhatsApp account already belong to the Client;
- the Developer removes its own access and asks the Client to withdraw the passwords, tokens and
  sharing it gave the Developer;
- the Developer deletes Client Data from its servers and backups within 30 days, and confirms this in
  writing on request.

11.5. Want to run the system yourself? Ask for a copy of the software and instructions (Agreement,
10.4).

## 12. Changes to these terms

The price of a plan and its AI limit change only as set in the Agreement (7.7): with 30 days' notice,
and only after a change by Meta or the AI provider. Any other change to these terms needs the
written agreement of both parties.
