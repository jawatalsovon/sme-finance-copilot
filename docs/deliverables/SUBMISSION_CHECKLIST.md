# Submission checklist

Deadline: **20 Oct 2026, 23:59 Beijing (21:59 Dhaka)**. The latest version received before the deadline counts. Official address (International AI Track): fintechathon@ican-x.com. Treat all six items as required (see `docs/research/OFFICIAL_RULES.md`).

| # | Required item | Where it is | Status |
|---|---|---|---|
| 1 | Technical documentation (architecture, core algorithms, security design) | `docs/deliverables/TECHNICAL_DOCUMENTATION.md` (export to PDF) | Done |
| 2 | Presentation deck (≤ 10 min) | `docs/deliverables/deck.pptx`, notes in `docs/deliverables/DECK_OUTLINE.md` (export to PDF) | Done. **Fill team and presenter names on slides 1 and 11** |
| 3 | Demo video (≤ 5 min) | Script: `docs/deliverables/DEMO_VIDEO_SCRIPT.md` | **Not recorded.** Record, export MP4 (H.264), upload (cloud link if too big) |
| 4 | Source code | GitHub repository `https://github.com/jawatalsovon/sme-finance-copilot` with `README.md` (quick start), `docs/`, `tests/` | Confirm the repo is **public** and the latest commit is pushed (the orchestrator/user pushes) |
| 5 | Security self-assessment | `docs/security/SECURITY_SELF_ASSESSMENT.md` (65 security tests pass) | Done |
| 6 | Execution evidence (sandbox logs + demo video) | `docs/evidence/SANDBOX_RUN_LOG.md`, `.json`, `README.md`; re-run with `python eval/demo_run.py` | Done (video pending, see 3) |

## Before sending
- [ ] No private or real company data anywhere in the repo, deck, video (CLAUDE.md rule). Check screenshots show only synthetic business names.
- [ ] Deck and documents exported to PDF (keep the originals too); all in English.
- [ ] Video ≤ 5:00, plays on another machine.
- [ ] Repo is public, `git log` shows the final commit, README renders.
- [ ] Registration is done and the subject line matches the organiser's format (rules: "International Track Registration – Country – Team Name – Team Leader's Full Name").
- [ ] Ask the organiser, if still unconfirmed: attachment size limit, whether a cloud link is accepted for the video.
- [ ] Send before the deadline with margin (aim for 18:00 Dhaka); keep a copy of the sent mail.

## Draft e-mail (you send it)

**To:** fintechathon@ican-x.com
**Subject:** FinTechathon 2026 International Track Submission – [Country] – [Team Name] – [Team Leader's Full Name]

Dear FinTechathon organising committee,

Please find the submission of team [Team Name] for the International AI Track, Topic E: **SME Finance Copilot**, an AI finance copilot that shows small-business owners the cash they can prove, explains the gap to their books with evidence, forecasts cash and matches public financing products. Every figure comes from a deterministic engine; the AI can only draft corrections, which the owner must approve, and no money-moving tool exists. All data is synthetic or publicly licensed.

Materials (all in English):
1. Technical documentation: TECHNICAL_DOCUMENTATION.pdf
2. Presentation deck: deck.pdf / deck.pptx
3. Demo video (about 5 min): [link / attached file]
4. Source code: https://github.com/jawatalsovon/sme-finance-copilot (README has quick-start and test instructions)
5. Security self-assessment report: SECURITY_SELF_ASSESSMENT.pdf (in the repository under docs/security/)
6. Execution evidence: SANDBOX_RUN_LOG.md / .json (repository under docs/evidence/) and the demo video

Team: [member names, universities].
Contact: [e-mail, phone].

If any file is too large or cannot be opened, please tell us and we will resend immediately. Thank you.

Kind regards,
[Team Leader's Full Name]
[Team Name]
