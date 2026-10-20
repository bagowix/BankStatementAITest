<!--
Operator note (hidden when rendered). Never send the raw .md file: send a PDF made from the
version for that client. The commands are in docs/sales/offers.md, section 1, item 7.
- Direct clients: the whole file, with these notes removed.
- Upwork clients: only after the contract has started, through Upwork messages, and only the
  platform version. It drops every block between the direct-only markers: lari prices and the
  pilot, both "Before you order" lines (demo bot link, test run on 20 emails), and the Contact
  block with {{GITHUB}}.
- Fiverr buyers: never. The gig is the price list (docs/sales/fiverr.md, section 1).
- Features still being built (WhatsApp, Google Calendar, PDF invoice fields) are NOT in the text
  below. The wording to add after each one is merged and described in docs/packages/*.md sits in
  "AFTER RELEASE" notes next to the place it goes.
- Before publishing, check the pre-publication list in docs/sales/offers.md, section 1.
-->

# AutoStudio — Services and Prices

![{{NAME}}]({{PHOTO}})

I'm {{NAME}}, a Python developer with {{YEARS}} years of experience, based in Tbilisi, Georgia.
I set up three ready-made automations for small businesses: an inbox assistant, a booking bot and
a competitor price monitor. Then I keep them running for a monthly fee. AutoStudio is one person.
You talk to the person who builds and runs your system.

Prices are in US dollars, valid from October 2026.

<!-- direct-only:start -->
Prices for businesses in Georgia are in lari, further down.
<!-- direct-only:end -->

## How every package works

- **Fixed price, fixed scope.** You pay once for setup. A monthly support plan keeps it running.
- **Nothing goes out without you.** Reply drafts wait in your Drafts folder. The bot answers only
  people who write to you. Nothing is sent in your name unless you set that up.
- **AI suggests, code checks.** I use AI models from OpenAI or Anthropic. Code checks their answers
  before you see them. A phone number, budget or price that isn't in the source is thrown out.
- **Your accounts, your data.** The Google Sheet, the Telegram bots and the mailbox belong to you.
  You can withdraw my access at any time.
- **A monthly report.** It shows what was done: emails sorted, bookings taken, prices checked. It
  also estimates the hours saved and shows how the estimate was made.

## At a glance

| Package | Basic | Standard | Premium |
|---|---|---|---|
| **Inbox** — every email sorted, every lead logged | $150 | $400 | $900 |
| **Booking bot** — bookings 24/7, a reminder before every visit | $200 | $450 | $950 |
| **Price monitor** — competitor prices in your sheet | $120 | $300 | $700 |

| Support plan | Care | Pro | Business |
|---|---|---|---|
| Per month | $49 | $149 | $349 |

---

## 1. Inbox — every email sorted, no lead forgotten

**The problem.** Leads, invoices and customer questions sit in one inbox. Some get a late answer.
Some get none.

**What you get.** New emails are checked every 10–15 minutes, and each one is sorted. Key details
go into a Google Sheet. A reply draft waits in your Drafts folder. You get a Telegram message about
a hot lead. Nothing is sent without you.

| | Basic | Standard | Premium |
|---|---|---|---|
| **Setup price** | **$150** | **$400** | **$900** |
| Mailboxes | 1 | 1 | up to 3, each with its own sheet and report |
| Categories | 5 standard: lead, support, invoice, marketing, other | up to 8 of your own | as many as you need |
| Urgency for every email | ✓ | ✓ | ✓ |
| Google Sheet row: sender, subject, one-line summary, name, company, phone, email, request, budget, language | ✓ | ✓ | ✓ |
| Phone, email and budget are saved only if they are actually in the email | ✓ | ✓ | ✓ |
| Newsletters and auto-replies are filtered out by rules, without AI | ✓ | ✓ | ✓ |
| Copies go to folders or labels. Nothing is deleted. Emails stay unread | ✓ | ✓ | ✓ |
| Emails that try to give orders to the AI are flagged, and nothing is done with them | ✓ | ✓ | ✓ |
| Optional: email addresses, names and phone numbers masked in the sheet (the subject and the summary are not) | ✓ | ✓ | ✓ |
| Reply drafts in your Drafts folder, in the sender's language, using only facts you approved. Never sent automatically | — | ✓ | ✓ |
| Hot-lead alerts from your own Telegram bot, or by webhook to Slack or Make | — | ✓ | ✓ |
| One custom addition, up to 6 hours of work (see below) | — | — | ✓ |
| Monthly report | ✓ | ✓ | ✓ |
| Delivery | 3 business days | 6 business days | 12 business days |
| Free fixes after launch | 14 days | 14 days | 30 days |

<!-- AFTER RELEASE (PDF invoice fields): add this row after "Hot-lead alerts":
| PDF invoices from emails go to an "Invoices" tab: supplier, number, date, total, currency | — | ✓ | ✓ |
-->

**Premium custom addition — pick one:**
- your own fields to extract, such as an order number or travel dates;
- each new lead sent to your CRM, Zapier or Make by webhook.

<!-- AFTER RELEASE (PDF invoice fields): add a third option:
- invoice data as a CSV file in the column layout your accountant uses.
-->

**Works with:** Gmail, Google Workspace, Zoho, Yandex and other mailboxes with IMAP access. For
Google Workspace, your admin may need to allow access. Outlook and Microsoft 365 can work through a
forwarding rule to a separate mailbox, if your Microsoft 365 admin allows forwarding outside your
organisation. I check this before you pay. Reply drafts then appear in that separate mailbox.

<!-- direct-only:start -->
**Before you order.** I show you the assistant on a set of sample emails. To see it on your own mail
first, we sign a short data processing agreement, and then I sort your last 20 emails as a test
run. Nothing in your mailbox changes, and you can withdraw access right after.
<!-- direct-only:end -->

**What you provide**
- Mailbox access with an app password, not your main password. You can withdraw it at any time.
- Your categories and what should happen with each one: draft, alert, folder.
- The facts a draft may use: prices, hours, how to order, cancellation policy, address (Standard,
  Premium). If a draft mentions a figure that isn't in your facts, the draft starts with a warning.
- 20–50 example emails, or a test run on your last 20 emails after we sign the data processing
  agreement.
- A Google Sheet shared with my service account. I send you the address.
- For alerts (Standard, Premium): a Telegram bot of your own, which you create in a minute with
  @BotFather, and your Telegram account. Or a Slack or Make webhook.
- A signed data processing agreement, before I connect to the mailbox. I provide the template.

**Not included**
- Sending emails. Drafts are never sent automatically.
- Deleting emails.
- Reading attachments, including PDF invoices. Invoice emails are sorted and filed; reading the
  invoice itself is custom work for now.
- A direct connection to Outlook or Microsoft 365. Forwarding can work (see above).
- A direct export to QuickBooks, Xero or HubSpot, or invoice reconciliation. These are custom work.
- Instant processing: new emails are checked every 10–15 minutes.
- Cold outreach or bulk email.

<!-- AFTER RELEASE (PDF invoice fields): replace the "Reading attachments" line with:
- Attachments other than PDF invoices. Invoice PDFs must contain text: scans and photos are custom
  work.
-->

**Proof.** I built an agent that reads bank statements from PDF files, including scans. It extracts
every transaction and checks each period against the balances printed in the statement. If the
numbers don't add up to the cent, the period is marked "not verified", with the size of the gap and
a likely cause. It is never passed as correct. I tested it on real statements from four different
banks. Three were fully reconciled without any paid AI. The fourth was a 99-page bundle of ten
poorly scanned statements: all ten were reconciled with two AI models, for under $1 in total. This
is a small sample, not a benchmark. The inbox package follows the same rule: a phone number or
budget that isn't in the email never reaches your sheet.

---

## 2. Booking bot — bookings around the clock, a reminder before every visit

**The problem.** Customers write when you are busy or closed. Some book somewhere else. Some forget
their appointment and don't come.

**What you get.** A Telegram bot that takes bookings 24/7 and reminds customers before the visit.
Customers can cancel or move a booking themselves. You get a message for every booking.

| | Basic | Standard | Premium |
|---|---|---|---|
| **Setup price** | **$200** | **$450** | **$950** |
| Channel | Telegram | Telegram | Telegram |
| Staff with their own schedules | 1 | up to 3 | up to 10 |
| Booking with buttons: service, staff, day, time, name and phone, confirm | ✓ | ✓ | ✓ |
| Free times from your hours, breaks and days off. The same time can't be booked twice | ✓ | ✓ | ✓ |
| Reminders 24 hours and 2 hours before the visit (you can change the times) | ✓ | ✓ | ✓ |
| Customers cancel or move a booking themselves, up to the cut-off you set | ✓ | ✓ | ✓ |
| A Telegram message to you for every booking, cancellation and change | ✓ | ✓ | ✓ |
| Your schedule in the bot: /today, /tomorrow, /week | ✓ | ✓ | ✓ |
| Understands plain messages like "tomorrow after 5 for a haircut?" | simple rules: dates, times, service names | AI | AI |
| Answers questions from your FAQ only. Other questions come to you, and you reply in Telegram | — | ✓ | ✓ |
| Bookings log and schedule in Google Sheets | — | ✓ | ✓ |
| Your own wording for every bot message. A shared chat for your staff | — | — | ✓ |
| Languages for your customers | English, Russian | English, Russian | English, Russian |
| Monthly report, including how many bookings came in while you were closed | ✓ | ✓ | ✓ |
| Delivery | 4 business days | 7 business days | 14 business days |
| Free fixes after launch | 14 days | 14 days | 30 days |

<!-- AFTER RELEASE (WhatsApp adapter): Channel row, Premium cell: "Telegram and WhatsApp, on your
own WhatsApp Business account"; Premium delivery: "14 business days, plus Meta's review time".
AFTER RELEASE (Google Calendar sync): add this row after "Bookings log":
| Each booking appears in your Google Calendar | — | ✓ | ✓ |
-->

A booking is made only when the customer presses "Confirm". The AI can read a message, but it can't
book or cancel anything on its own. Names and phone numbers from the booking are not sent to the AI.

<!-- direct-only:start -->
**Before you order.** Ask for the demo bot link and make a test booking yourself.
<!-- direct-only:end -->

<!-- AFTER RELEASE (WhatsApp adapter, with the opt-in step at booking checked in the code): add this
section here.

**WhatsApp (Premium) — what to know**
- The bot runs on your own WhatsApp Business account and number, through Meta's WhatsApp Cloud API.
  The account is yours. I am added as an admin.
- Meta charges for WhatsApp messages. Meta sets the rates and bills your card directly in WhatsApp
  Manager. I don't resell messages or add a margin.
- Reminders use message templates that Meta has to approve. Customers agree to receive them when
  they book.
- Is your number already in the WhatsApp Business app? Then there are two options. The number moves
  to the bot, and the app's chat history on that number is lost. Or it connects through a Meta
  partner, so you can keep using the app. The partner charges its own monthly fee, and I quote this
  option separately.
- Meta allows a business to run its own booking and FAQ bot. Meta doesn't allow general-purpose AI
  assistants. This bot handles bookings and your FAQ only.
-->

**What you provide**
- Your services with duration and price. Your staff and their hours. Opening hours, breaks and
  holidays.
- Your cancellation rule, for example "up to 2 hours before".
- 5–10 common questions and their answers (Standard, Premium).
- A short call to create the bot on your own Telegram account, so the bot belongs to you. You and
  your staff press /start once.
- An empty Google Sheet, shared with my service account (Standard, Premium).

<!-- AFTER RELEASE: "An empty Google Sheet and your Google Calendar, shared with my service account
(Standard, Premium)." WhatsApp: "For WhatsApp: a Meta Business account, a phone number, a payment
card in WhatsApp Manager, and your approval of the consent text (Premium)." -->

**Not included**
- WhatsApp, Instagram and Facebook Messenger: custom work for now. Phone calls and voice.
- Online prepayment. Tracking whether a customer came.
- Sync with other booking software such as YCLIENTS, Altegio or Fresha.
- Group classes, where more than one person books the same slot.
- More than one location or time zone per bot. Each location is a separate setup.
- A general chatbot. Promotions or mass messages.
- Storing medical information. A clinic's bot keeps only the name, phone, service and time. What a
  customer types is sent to the AI as written (Standard, Premium), so for clinics I recommend Basic,
  which uses no AI, or a line in the bot asking customers not to type medical details.
- A Georgian-language bot. Available on request and quoted separately; the Georgian text is
  written by a native speaker.

<!-- AFTER RELEASE (WhatsApp adapter): first line becomes "Instagram and Facebook Messenger. Phone
calls and voice." and add "WhatsApp message fees, which Meta charges you directly." -->

---

## 3. Price monitor — competitor prices come to your sheet

**The problem.** You check competitor prices by hand, in dozens of browser tabs. By the time you
notice a change, the sales are gone.

**What you get.** Competitor prices next to yours in one Google Sheet, updated on a schedule. A short
Telegram message when a price changes or a competitor becomes cheaper than you.

A **link** is one product on one competitor's site. 50 products on 3 sites make 150 links.

| | Basic | Standard | Premium |
|---|---|---|---|
| **Setup price** | **$120** | **$300** | **$700** |
| Competitor sites | up to 3 | up to 10 | up to 20 |
| Links | up to 200 | up to 1,000 | up to 2,000 |
| Checks | once a day | every 6 hours | up to once an hour* |
| "Prices now" tab: each competitor price and its difference from yours, in % | ✓ | ✓ | ✓ |
| "History" tab: each new price with the page text it was read from | ✓ | ✓ | ✓ |
| "Changes" tab: price moves, "cheaper than you", out of stock or back in stock | ✓ | ✓ | ✓ |
| A Telegram message, sent only when something changed | once a day | after each check | after each check |
| If a page can't be read, you see the last known price, marked as such. Never an invented number | ✓ | ✓ | ✓ |
| AI reads pages that have no price markup. A price is accepted only if the AI's quote is found on the page and gives the same number | — | — | ✓, up to 20 pages per check |
| Monthly report | ✓ | ✓ | ✓ |
| Delivery | 3 business days | 5 business days | 10 business days |
| Free fixes after launch | 14 days | 14 days | 30 days |

\* The monitor waits at least 5 seconds between requests to the same site, and longer if the site
asks for it. That is at most about 700 pages an hour from one site. Before you order, I check that
your links fit the schedule you want.

**How prices are collected.** Only public pages that anyone can see without logging in. I follow
each site's robots.txt rules, and make an exception only with the site owner's written permission.
Requests carry the monitor's name and a contact email, so a site owner can reach me. Your name is
never in the requests. If a site owner asks me to stop, I stop the same day.

**Before you order.** Send me up to 3 competitor pages. I tell you for free whether the price can be
read. A site that can't be read is left out of the quote, before you pay.

**What you provide**
- Your products: ID, name, your price and currency.
- For each product, links to the same product on competitor sites, in the exact variant (color,
  size, volume).
- A Google Sheet shared with my service account.
- For alerts: a Telegram bot of your own, which you create in a minute with @BotFather, your
  Telegram account, and your alert threshold. The default is a 3% price change.
- Written confirmation that, as far as you know, the sites' terms allow monitoring these pages.

**Not included**
- Pages behind a login or paywall. Sites with a CAPTCHA or bot protection: I don't get around them,
  and I don't use proxies to hide.
- Prices that appear only after JavaScript runs in a browser. This is custom work.
- Finding matching products for you. You give me the links; automatic matching is custom work.
- Dashboards beyond Google Sheets. Custom work.
- Checks more often than once an hour.
- Changing your own prices automatically.
- Personal data, such as seller names on marketplaces.

---

## Support plans

Hosting is part of every plan. A plan starts on the day of launch and is required while I host your
system. The free fixes after launch come on top of it. Prefer to host it yourself? Ask me for a
quote.

| | Care | Pro | Business |
|---|---|---|---|
| **Per month** | **$49** | **$149** | **$349** |
| Per week, if you hired me on Upwork | $12 | $35 | $81 |
| Hosting, uptime monitoring, daily backups, monthly security updates, monthly report | ✓ | ✓ | ✓ |
| I reply within | 2 business days | 1 business day | 4 business hours |
| Hours for changes: prices, services, hours, FAQ, categories, competitors, texts | 1 hour | 4 hours | 10 hours |
| Repairs after outside changes: a competitor redesigns their site, a password changes, a provider changes its service | counts toward your 1 hour | included | included |
| Fixes for my own bugs | always free | always free | always free |
| AI operations per month | 2,000 | 10,000 | 30,000 |
| One new small automation per quarter, up to 6 hours. For example, a weekly Telegram summary or a new report tab | — | — | ✓ |

Business hours are Monday to Friday, 10:00–19:00 Tbilisi time (UTC+4), except Georgian public
holidays. Monitoring alerts me, not you, when a scheduled run fails or the service stops responding.

**What counts as an AI operation.** One request to the AI model:
- an email sorted = 1, or 2 with a reply draft;
- a customer's typed message in the bot = 1, or 2 if it's a question the bot answers;
- a competitor page read by AI = 1.

Buttons, newsletters filtered by rules, and prices read from page markup are not counted. The Basic
booking bot uses no AI at all.

**If you reach the limit.** The limit is a monthly spending cap on the AI key that only your system
uses. There are no charges above it. When it is reached, I tell you, and the AI pauses until the
1st, or until you move to a higher plan:
- the booking bot keeps taking bookings with buttons and sending reminders, and reads typed
  messages with simple rules, in English and Russian;
- new emails wait, unsorted, with no drafts or lead alerts, and are sorted when the AI is back;
- competitor pages without price markup show "not checked this time"; prices from page markup are
  checked as usual.

If you reach the limit two months in a row, I'll suggest the next plan. Prefer no limit? Use your
own AI account: you then pay the AI provider directly.

**Extra hours** cost $40 an hour, in 30-minute steps, and only after you approve the estimate.
Unused hours don't carry over. If you go over your plan two months in a row, I'll suggest the next
plan.

**Not included in support:** new features, new integrations or channels, and the fees and
subscriptions of other services you use.

**Cancelling.** Plans are monthly. Cancel any time. The service runs to the end of the paid month.
Your Google Sheet and Telegram bots are already yours. On request, I export your bookings or price
history as CSV files. Then I delete your data from my server within 30 days.

---

## Custom work

Custom work is quoted per client. I estimate the hours before starting, and we agree a fixed price
at $40 an hour. Payment terms are the same as for setup.

- Direct export to QuickBooks, Xero or HubSpot. Invoice reconciliation and duplicate checks.
- Reading invoice PDFs from emails. Scanned or photographed documents.
- Bank statements to a spreadsheet, with every period checked against the printed balances. Quoted
  after a test on your sample statements.
- A direct Outlook or Microsoft 365 connection.
- WhatsApp for the booking bot, on your own WhatsApp Business account. Instagram or Facebook
  Messenger. Online prepayment for bookings.
- A Georgian-language booking bot.
- Price monitoring of sites that need a full browser. Automatic product matching. Dashboards.

<!-- AFTER RELEASE: remove "Reading invoice PDFs from emails" (PDF invoice fields) and "WhatsApp for
the booking bot, on your own WhatsApp Business account" (WhatsApp adapter) from this list. -->

I don't build voice assistants, general-purpose chatbots, cold outreach, or lead lists scraped from
the web.

---

<!-- direct-only:start -->
## Prices for businesses in Georgia (GEL)

The same packages and plans as above, paid in lari.

| Package | Basic | Standard | Premium |
|---|---|---|---|
| Inbox | 390 ₾ | 990 ₾ | 1,990 ₾ |
| Booking bot | 390 ₾ | 890 ₾ | 1,690 ₾ |
| Price monitor | 290 ₾ | 690 ₾ | 1,490 ₾ |

| Support plan | Care | Pro | Business |
|---|---|---|---|
| Per month | 99 ₾ | 249 ₾ | 590 ₾ |

Extra hours and custom work: 100 ₾ an hour. Payment by bank transfer in lari to a Georgian bank
account.

**Pilot: 50% off setup for the first three businesses in Georgia.** In return, you agree to a case
study. After two months, I publish your results from the monthly report, and you give a 20-minute
interview. You approve the text before it goes live. Your name and logo appear only if you agree.
I don't ask for ratings or reviews on Google Maps or anywhere else. The published case study says
you received a discount. Support plans are at the regular price.

---
<!-- direct-only:end -->

## Terms

**Payment.** For setup, you pay 50% before I start and 50% after you accept the work. Support is
paid monthly in advance, from launch.

**Upwork and Fiverr.** If you found me on Upwork or Fiverr, every payment goes through that
platform, including setup and support. Support is a separate contract or order there, starting at
launch. I use the platform's milestones, weekly payments or recurring orders. I will never ask you
to pay outside the platform.

<!-- direct-only:start -->
**Direct clients.** You get an invoice in US dollars, with the payment methods listed on it.
Businesses in Georgia pay in lari by bank transfer. The support invoice comes on the 1st, and
payment is due within 7 days. The first month is charged by days, from launch, with the final
setup invoice.
<!-- direct-only:end -->

**Delivery time** starts when I have everything from "What you provide" and the first payment.
Approvals by others, such as your Google Workspace or Microsoft 365 admin, are outside my control
and are not counted.

**Revisions.** Before launch, you get two rounds of changes within your package: texts,
categories, FAQ, alert settings. Anything new is a change request with a quote.

**Acceptance.** I show you the system working on your real data. You have 5 business days to report
anything that doesn't work as described here. If I hear nothing, the work is accepted. On Upwork
and Fiverr, the platform's acceptance rules apply.

**Free fixes.** For 14 days after launch (Basic, Standard) or 30 days (Premium), I fix for free
anything that doesn't work as described here. After that, my own bugs are always fixed free while
you're on a support plan.

**Price changes.** Your setup price doesn't change once agreed. A support price can change with 30
days' written notice, and you can cancel instead. This can happen in two cases:
- An AI provider changes its prices, which changes what your AI operations cost.
- If your bot uses WhatsApp: Meta changes its WhatsApp prices or rules. Meta changes prices only on
  the first day of a quarter, with at least a month's notice.

**Your data.** I sign a data processing agreement with you and keep only what the work needs. For
the booking bot, that is the customer's name, phone, service and time. For the inbox, I keep the
sorting results, not your email texts. The AI providers (OpenAI or Anthropic) receive only what each
task needs, under their API terms. I share their current terms with the agreement.

**What I don't promise.** I don't promise more revenue, a set number of bookings or a set drop in
no-shows: these depend on your customers. AI can make mistakes. That's why drafts are never sent on
their own, a price needs a matching quote from the page, and the bot books only on "Confirm". Not
every competitor site can be read, and I tell you which ones before you pay.

<!-- direct-only:start -->
---

## Contact (direct clients)

- {{NAME}}
- Email: {{EMAIL}}
- Telegram: [@{{TELEGRAM}}](https://t.me/{{TELEGRAM}})
- WhatsApp: [+{{WHATSAPP}}](https://wa.me/{{WHATSAPP}})
- Book a 15-minute call: {{CALENDAR_LINK}}
- Website: https://{{DOMAIN}}
- Code samples, including the bank statement agent: {{GITHUB}}
- I reply in {{LANGUAGES}}.
<!-- direct-only:end -->
