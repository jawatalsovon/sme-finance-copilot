# Official rules — FinTechathon 2026 International Track (Topic E)

Researched 2026-10-17 by R1. Sources (all read in full, paraphrased here):

- S1 — Official site, rules page, English text: <https://fintechathon.g-ican.com/#/rule> (International tab, "English"). The site is a JavaScript app; plain fetchers return only the title. Read it in the in-app browser.
- S2 — Official site, Chinese text of the same page (the Chinese version prevails on any conflict, per rule 04 of the Competition Statement).
- S3 — Official site home page, "Topics" and "Schedule" sections: <https://fintechathon.g-ican.com/#/>
- S4 — Appendix 2, "Problem Description and Competition Rules for International Track" (PDF, one page, mirrored by ITS): <https://www.its.ac.id/international/wp-content/uploads/sites/66/2026/10/PBCSF-Appendix-2-Problem-Description-and-Competition-Rules-for-International-Track.pdf>
- S5 — IEEE CIS Shenzhen announcement: <https://r10.ieee.org/shenzhen-cis/blog/2026/06/17/2026-shenzhen-international-fintech-competition-international-track/>
- S6 — ITS announcement: <https://www.its.ac.id/international/2026/10/06/2026-shenzhen-fintechathon-international-track-a-fully-funded-international-competition/>

## 1. Deadline and timeline (all UTC+8)

| Date | Event | Source |
|---|---|---|
| 31 Aug – 20 Oct 2026 | Registration and submission window. Registration and submission share one deadline. | S1, S4 |
| **20 Oct 2026, 23:59** | Deadline per S4 (the PDF also gives 15:59 UTC = 21:59 Dhaka). S1 says "24:00 on 20 October". **Plan for 23:59 Beijing = 21:59 Dhaka.** | S1, S4 |
| 21 Oct | Compliance screening and International-Track eligibility check | S1 |
| 22–23 Oct | Online review by WeBank experts and scholars; top 10 teams become finalists | S1 |
| 31 Oct | Finalists announced; invitation letters issued | S1, S4, S5 |
| 22 Nov | Closed-door English defence at WeBank, Shenzhen (S1) | S1 |
| 23 Nov | Awards ceremony (S1) | S1 |

Dates conflict between sources. S4 gives: online review 20 Oct, check-in and WeBank HQ visit 20 Nov, grand final 21–23 Nov. S5 gives review 23–28 Oct. S1 is the newest, most specific and the one that governs, so use S1. The exact dates only matter if we reach the finals.

- Submissions can be updated any time before the deadline; the latest version received counts. Nothing is accepted after the deadline (S4).
- A bilingual reminder email goes out 24 h before the deadline (S4).
- The committee may change the schedule, format, judging or defence arrangements and will notify teams by website, email or other channels (S1).

## 2. Submission checklist

S4 lists six items. S1 (the website) gives a different status for each:

| # | Item | Format / limit | Status in S4 (PDF) | Status in S1 (website, prevails) |
|---|---|---|---|---|
| 1 | Technical documentation | System architecture, core algorithm description, security design. No page limit stated. | listed | **Required** |
| 2 | Presentation deck | Presentation of at most 10 minutes | listed | **Required** |
| 3 | Demo video | At most 5 minutes | "optional" | **Required** |
| 4 | Source code | GitHub repository link, README, deployment instructions, test cases | listed | Optional |
| 5 | Security self-assessment report | Permission-tier implementation and known-risk list | listed | Optional |
| 6 | Execution evidence | Non-on-chain topics: simulated-account or sandbox operation logs, plus the demo video | listed | Optional |

- **Contradiction on the demo video.** S4 says optional; S1 says required. Treat it as required.
- **Treat all six items as required.** The "optional" items 4–6 are exactly what the 40% and 30% score dimensions are judged on, so omitting them would be costly.
- No file format is specified for any item (no "PDF only" or "MP4 only" rule found). Use PDF for documents and the deck, MP4 (H.264) for video, and a public GitHub URL for code. All materials must be in English; submitted materials must include an English version (S1 rule 4).
- **Where to submit:** The site says to register "by official email" and to submit the English materials there (S1). The official enquiry address for the International AI Track is **fintechathon@ican-x.com** (S1). S4 lists a different contact, wangyr@pbcsf.tsinghua.edu.cn. S5 says to download a registration form from the official site and email it, with subject "International Track Registration – Country – Team Name – Team Leader's Full Name", and that receipt is confirmed within 48 hours. **Unconfirmed:** I did not find the registration-form download link on the page. Ask the organiser by email, and do it today, to confirm the submission channel and attachment size limit. Large video files may need a cloud link rather than an attachment.
- The site header also shows a button "人工智能赛道报名" linking to <https://apply.g-ican.com>. It is not stated whether this serves the International Track; the International rules page says email. I did not open it. Jawat should check it before registering, because the Domestic and International tracks apparently use different intake routes.

## 3. Eligibility and team

- Open to **full-time students** (undergraduate, master's or PhD) enrolled at **universities or research institutes outside China** (Mainland China, Hong Kong, Macao and Taiwan all count as "China" for this rule). Any nationality, major or year (S1).
- Team of **2–5**. Every member must be at an institution outside those regions (S4). Cross-year, cross-major, cross-university and cross-border teams are allowed.
- **At least 60% of the team must be non-Chinese nationals**, rounded up: a 3-person team needs at least 2 non-Chinese members (S1).
- Each person can join only one team across the Domestic and International tracks; duplicate registration is void (S1).
- Registration requires, for every member: name, nationality, passport or other ID type and number, institution and **proof of enrolment**, major, year, contact details (S1). **Flag for Jawat:** this needs real documents for every member; agents cannot do it.
- The organiser decides track assignment from nationalities, institutions and documents, and may move or disqualify a team that does not fit (S1).
- No registration fee (S1, S4).

## 4. Scoring rubric (S1, S4)

The same dimensions apply to all six topics.

| Weight | Dimension | What the rules say it measures |
|---|---|---|
| 40% | Task completion | Completion of **scripted tasks in a simulated environment** for non-on-chain topics. |
| 30% | Security & compliance | Defence against **induced transfers, data extraction, privilege escalation and prompt injection**. Serious security failures trigger substantial deductions. |
| 30% | Innovation & interaction | Scenario novelty, real-world value, **dialogue efficiency, clarification, correction and fallback** capability. |
| +0–5 | Challenge / implementation bonus (capped) | Rewards advanced topics or **demonstrated real-world deployment**. S4 ties it to on-chain exploration; S1 words it more broadly ("real-world deployment"). Topic choice itself does not change the base score. |

- Appeals can be filed in writing within three business days after results are announced (S1).
- Review is by an online panel of WeBank industry experts and university scholars (S4).

Implications for the build:

1. "Scripted tasks in a simulated environment" suggests the judges may run scripted prompts or scenarios against the system. **The demo should run end to end from a clean start, with fixed seeded data and the mock or real LLM, and each flow should be re-runnable.** Include a script that replays the main tasks, and put its logs in the execution evidence.
2. The security test targets match the PLAN: injection, exfiltration of data, over-privileged actions and induced transfers. The "no money movement" rule is the strongest answer to induced transfers. **Add a red-team table to the self-assessment: attempts to make the agent transfer money, reveal other users' data, skip approval, or follow instructions hidden in a CSV, with the observed results.**
3. Innovation scoring names clarification questions, correction and fallback. The agent should ask when mapping is ambiguous, accept the owner's correction, and degrade gracefully (for example "I cannot verify this number" instead of guessing).
4. The "real-world deployment" bonus favours a short note, with no figures, that the pilot ran on an actual business (private the pilot company data). The note should be generic and must not give figures; check it against the CLAUDE.md privacy rule.

## 5. Topic E specifics

- Official wording, S4: "A finance copilot for small-business owners, offering financing-option matching, cash-flow management with alerts, and business financial analysis."
- Home-page topic list, S3: "E — SME finance assistant: cash flow, **reconciliation** and business advice (现金流、对账与经营建议)". This wording **adds reconciliation (对账) explicitly**, which supports the Cash Truth Gap differentiator. Cite it in the deck.
- The overall theme is "AI as a Financial Participant": an agent that completes real tasks in a financial scenario (S4). Topic framing on the site: a Financial Agent that can "understand, plan, and execute safely" (S3, translated).
- No dataset, API or sandbox is provided or specified for Topic E that I could find. The home page lists two technical open-class videos for 2026 on Bilibili: <https://www.bilibili.com/video/BV1Z1hk68Esc/> and <https://www.bilibili.com/video/BV1Eyhk6wEUk/>. I did not watch them (Chinese-language video, and the content is not readable by these tools). **Jawat or a Chinese-speaking teammate could skim them for an evaluation harness or environment hints.** They may be domestic-track focused.

## 6. Third-party data, code, APIs, IP

- All data, code, open-source components, **APIs** and other third-party material must be legally authorised and comply with the open-source licences and data-use terms. The team bears the liability (S1 rule 6). Hence `docs/LICENSES_DATA.md`.
- Entries must be independently completed, with no plagiarism or IP infringement; a verified violation can cost eligibility and results (S1 rule 5).
- **IP stays with the contestants.** The host may use the entry and team information for non-commercial promotion (website, publications, exhibitions, authorised media), under a non-exclusive, royalty-free licence (S1 rule 7; S4). **Consequence for private data:** anything in the submission can be republished, so no the pilot company data or any real customer data may be in the repo, demo video or deck. This matches CLAUDE.md.
- Governing law is the law of the PRC; where the Chinese and English notices differ, the Chinese prevails (S1).
- **Real third-party APIs and sandboxes:** nothing in the rules forbids them or prefers them. They are allowed provided the licence and terms permit the use (rule 6 covers "APIs"). The execution-evidence item names "simulated-account / sandbox operation logs" as the expected proof for non-on-chain topics, so sandbox use (QuickBooks sandbox, etc.) matches the organiser's own wording. A sandbox is the right choice; production systems are unnecessary.
- No clause was found on the use of LLM providers, on data residency, or on privacy impact beyond the "Privacy Policy" that S4 says is published on the site (**not found** — the rules page does not link it).

## 7. Differences from `docs/PLAN.md` section 0

| PLAN.md says | Official sources say | Action |
|---|---|---|
| Deadline 20 Oct 23:59 Beijing = 21:59 Dhaka | Same in S4; S1 says "24:00" | No change; keep 23:59 as the working time. |
| Presentation deck "for a final presentation of at most 10 minutes" | Same | No change. Note the deck is also what gets judged in online review (22–23 Oct), not only for finals. |
| Demo video at most 5 minutes (listed without status) | **Required** on the website, "optional" in the PDF | Keep as required. |
| Source code, security report, evidence listed as items | Website marks them optional | Keep as required; they carry the 30% security score. |
| Team: "2–5 students, ≥ 60% non-Chinese nationals" (PLAN section 7 risk 2) | Correct, and adds: **full-time students at an overseas university**, ID and enrolment proof for each member, and registration **by email** (not via the website form) | Update risk 2: add "all members are enrolled students outside China; each needs passport number and proof of enrolment". |
| "Registration done" presented as a task for today | Registration is by email to the organiser; the form location is unconfirmed (see section 2) | Jawat should email fintechathon@ican-x.com today to confirm the form and the submission method. |
| Scoring 40/30/30 | Same, plus a 0–5 bonus | Add the bonus. |
| Execution evidence = simulated-account logs + demo video | Matches | No change. |
| (not stated in PLAN) | Judging is "scripted tasks in a simulated environment", plus explicit tests for induced transfers, data extraction, privilege escalation and injection | Add to PLAN section 6: make flows replayable by script, and add a red-team table. |
| (not stated in PLAN) | Online review is 22–23 Oct, i.e. **the deadline-to-review gap is 1 day**; finals are in Shenzhen from 22 Nov and one member must attend in person | Mention in the risks: a finalist travelling to Shenzhen needs a passport and visa (the organiser pays travel and accommodation for invited members). |

## 8. Open questions for the organiser (email fintechathon@ican-x.com)

1. Where is the registration form, and is it emailed to the same address as the submission? What is the maximum attachment size? Can we send a cloud link for the video?
2. Is the demo video required? (website says yes, PDF says optional)
3. Are source code, the self-assessment and the execution evidence required or optional? Is a GitHub link enough?
4. Are the scripted tasks published or run against our deployed system? If the latter, what interface do they expect (HTTP API, UI, chat)?

These are best sent now; the deadline is in three days.
