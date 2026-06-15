# Non-duplication analysis

A deliberate map of adjacent efforts and why Hidden Health Data fills a gap rather
than repeating them. Short version: **everyone else organizes *structured* data
(claims, surveys, surveillance) or publishes *curated summaries/links*. No one
produces an open, row-level, provenance-stamped dataset extracted from the
*narrative regulatory and assessment PDFs* themselves.**

## The landscape at a glance

| Effort | Input | Output | Open row-level dataset from PDFs? | Relationship to us |
|---|---|---|---|---|
| **Cornell Medicaid Policy Impact Initiative / Medicaid Atlas** (Weill Cornell + BU) | T-MSIS **claims** (TAF) | 10–15 **spending & utilization** measures, web platform | No (claims, not documents) | **Complementary + linkable** |
| **Medicaid Data Learning Network** (AcademyHealth) | TAF claims | Best-practice methods for TAF | No | Complementary (methods, not docs) |
| **T-MSIS Analytic Files / ResDAC** (CMS) | Claims/enrollment | Restricted analytic claims files | No | Upstream claims source |
| **CMS Medicaid.gov EQR page** | — | **Links** to state reports + protocols | No (links only) | We structure what they only link |
| **MACPAC** | Mixed | Periodic issue briefs/analyses | No | Cites the gap; not a dataset |
| **KFF** (e.g., 1115 tracker) | Mixed | Curated **summaries**/status | No | Curated prose, not extraction |
| **SHADAC** (U Minn) | Surveys | State health-access estimates | No (survey-based) | Different source |
| **County Health Rankings** (UWPHI) | Aggregated indicators | County composite measures | No | Different source |
| **CDC PLACES / WONDER** | Surveillance/modeled | Small-area estimates | No | Different source |
| **HCUP** (AHRQ) | Hospital discharge | Encounter datasets | No | Different source |
| **Hilltop Institute** | Statutes | Community-benefit **law** profiles | No (law, not CHNA contents) | We'd extract CHNA *contents* |

## Cornell, specifically

The user asked us to be certain we do not overlap with the **Cornell Medicaid data
initiative**. We looked closely. The relevant program is the **Medicaid Policy
Impact Initiative** at the Cornell Health Policy Center (directed by Dr. William
Schpero), whose flagship is the **Medicaid Atlas** — a national web platform built
with Boston University and funded by Arnold Ventures
([announcement, 2026](https://news.weill.cornell.edu/news/2026/04/grant-supports-efforts-to-create-atlas-of-medicaid-spending);
[Cornell Health Policy Center](https://healthpolicycenter.cornell.edu/chpc/initiatives/)).

It is built on the **T-MSIS Analytic Files (TAF)** — modernized national Medicaid
**claims** — to surface **spending and utilization** drivers across programs,
plans, and populations. Cornell is also a founding member of the AcademyHealth
**Medicaid Data Learning Network**, which develops TAF best practices
([Weill Cornell](https://phs.weill.cornell.edu/news/weill-cornell-researchers-lead-development-checklist-improve-medicaid-policy-research)).

How Hidden Health Data differs — on every axis:

| Axis | Cornell Medicaid Atlas | Hidden Health Data |
|---|---|---|
| **Input** | Structured claims (TAF) | Narrative regulatory/assessment **PDFs** |
| **Method** | SQL/analytics on claims | LLM structured **extraction** of documents |
| **Output** | Spending & utilization measures | **Quality, PIPs, compliance, community needs** |
| **Form** | Curated web platform | Open, row-level dataset + extraction code |
| **Coverage of MCO oversight** | Limited — **managed-care plan payments are redacted in TAF** | EQR quality/compliance is exactly this layer |

Crucially, the two are **complementary and joinable**: our EQR records are at the
**state × plan × measure** grain, which lines up with the plan-level spending and
utilization the Atlas reports. A researcher could ask "do higher-spending plans
post better quality and fewer compliance findings?" — a question *neither* dataset
can answer alone. We see coordination (shared plan identifiers, cross-citation),
not competition, as the goal. We will reach out before any public release.

## What would count as overlap (and our guardrails)

- If another group published an **open, structured, row-level EQR dataset**, we
  would pivot to enrichment/coverage rather than duplicate it. As of this writing
  we found none — only the CMS link page and MACPAC analyses.
- For datasets where a standardized internal system exists (e.g., **CDC ERASE
  MM/MMRIA** for maternal mortality), we extract the **published reports and
  recommendations**, and will coordinate rather than re-abstract case data.
- The registry records an explicit `existing_efforts` block per dataset so this
  check is part of the data model, not an afterthought.
