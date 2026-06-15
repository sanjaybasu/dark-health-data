# The landscape of missing & buried public-health data

*A short literature review motivating Hidden Health Data. Companion to the
machine-readable catalog in [`registry/datasets.yaml`](../registry/datasets.yaml).*

## 1. Two kinds of "missing"

Public-health data can be missing in two very different ways:

1. **Never collected ("dark data").** Data that was never gathered — often about
   the people who most need attention. As a 2025 analysis in the *Journal of
   Public Health Policy* puts it, in public health these absences are "rarely
   accidental" and reflect "systemic exclusion where populations are overlooked,
   questions unasked, and lived experiences undervalued"
   ([Dark Data in Public Health, 2025](https://link.springer.com/article/10.1057/s41271-025-00589-3)).
   This is **structural missingness**: the groups we most need to study are the
   ones about which we have the least information
   ([*PMC* 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8607058/)).

2. **Collected but buried.** Data that *was* gathered, is often *legally required
   to be disclosed*, and is technically "public" — but is published as
   unstructured PDFs scattered across thousands of agency, hospital, and regulator
   websites, with no common schema and no index. It is **theoretically available,
   practically inaccessible.**

Hidden Health Data targets the **second** kind. It is the more immediately
tractable problem — the documents exist and can be obtained — and modern LLM-based
extraction makes it newly solvable at scale. (The first kind is a data-*collection*
problem that no extraction pipeline can fix; we note it but do not claim to solve
it.)

## 2. Why this matters for Medicaid and underserved populations

Much of the buried regulatory record is precisely the part of the health system
that serves low-income and marginalized people: Medicaid managed-care oversight,
safety-net hospital obligations, maternal-mortality review, behavioral-health
block grants, nursing-home enforcement. The information needed to hold these
systems accountable — quality measures by plan, identified community needs,
preventability findings, compliance determinations — is overwhelmingly **narrative
text in PDFs**, not analyzable data. The result is an evidence gap exactly where
equity questions are sharpest.

## 3. Selection criteria

We prioritize a dataset family when it is:

1. **Legally public or already posted** (low legal/ethical risk; no PHI).
2. **Buried** — unstructured, decentralized, no machine-readable repository.
3. **High-value** for research/policy, ideally touching underserved populations.
4. **Structurable** — contains recurring, schematizable facts (measures, findings).
5. **Not already solved** by an existing open dataset (see
   [`non-duplication.md`](non-duplication.md)).
6. **Maintainable** — recurring publication cadence so the dataset stays current.

## 4. Catalog of candidate datasets

Full metadata (legal basis, cadence, volume, existing efforts) is in
[`registry/datasets.yaml`](../registry/datasets.yaml). Highlights:

| Dataset | What's buried | Underserved relevance | Why not already solved |
|---|---|---|---|
| **Medicaid EQR technical reports** *(built)* | Validated quality measures, PIPs, compliance reviews, per plan per state | High (Medicaid managed care) | Posted only as scattered PDFs; **no central repository** ([MACPAC, 2025](https://www.macpac.gov/wp-content/uploads/2025/03/MACPAC_March-2025-Chapter-1.pdf)) |
| **Hospital CHNAs** | Prioritized community needs + committed investments | High (community/SDOH) | ~95% public but uncatalogued across thousands of hospital sites; only one-off manual studies exist ([*PMC* 2023](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10285662/)) |
| **Maternal Mortality Review Committee reports** | Cause-of-death, preventability, recommendations | High (maternal equity) | State reports with no common schema; CDC ERASE MM standardizes internal abstraction, not published-report recommendations |
| **Medicaid 1115 demonstration evaluations** *(built)* | Tested services + evaluation findings (food/nutrition, housing, transportation) | High (Medicaid) | Long PDFs on Medicaid.gov; trackers summarize status, not row-level findings |
| **Nursing-home statements of deficiency (CMS-2567)** *(built)* | Narrative surveyor findings + plans of correction | Medium (dual-eligibles) | Care Compare exposes tags/counts, not the narrative "why" (ProPublica indexes narratives; we add structured records) |
| **Health-insurance rate filings (SERFF)** | Rate justification, assumptions | Medium | Decentralized state filings, mostly PDF |
| **SAMHSA block-grant applications/reports** | Behavioral-health spending plans | High | Long PDFs; not analyzable |
| **Opioid-settlement expenditure reports** | How settlement dollars are spent | High | Heterogeneous state/county PDFs |

## 5. Why Medicaid EQR is the first build

EQR technical reports score highest on every selection criterion: they are
federally mandated and public, genuinely buried (MACPAC notes the absence of a
central repository), rich in schematizable facts (HEDIS / Adult & Child Core Set /
CAHPS measures with numerators and denominators), squarely about Medicaid and the
populations it serves, recurring annually, and — critically — **not addressed by
any existing open dataset**, including the claims-based Medicaid efforts now under
way (see [`non-duplication.md`](non-duplication.md)).

## 6. A note on rigor

Counts and volumes in the catalog are approximate and will be refined as
connectors are built. The point of this review is not a precise census but a
defensible prioritization: there is a large, well-documented class of public-health
data that is public in principle and inaccessible in practice, it falls
disproportionately on underserved populations, and it is now extractable at scale.

---

### Sources

- Dark Data in Public Health. *J Public Health Policy*, 2025. https://link.springer.com/article/10.1057/s41271-025-00589-3
- Public health inequalities, structural missingness, and the digital revolution. *PMC*, 2021. https://pmc.ncbi.nlm.nih.gov/articles/PMC8607058/
- MACPAC, Examining the Role of External Quality Review in Managed Care Oversight (March 2025). https://www.macpac.gov/wp-content/uploads/2025/03/MACPAC_March-2025-Chapter-1.pdf
- Medicaid.gov, Quality of Care — External Quality Review. https://www.medicaid.gov/medicaid/quality-of-care/medicaid-managed-care-quality/quality-of-care-external-quality-review
- The public availability of hospital CHNA reports. *PMC*, 2023. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10285662/
- Content Analysis of Hospitals' Community Health Needs Assessments in the Most Violent Cities. *PMC*, 2023. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12342434/
