# Standards mapping (OMOP-like naming crosswalk)

The prototype keeps a small, readable schema. Each column also carries an `omop_like` field in
`catalog/catalog.yaml` that names the OMOP CDM v5.4 table and field where the value would live in
a shared model.

**Scope:**
- **What this is:** a *naming* crosswalk.
- **What this is not:** a vocabulary mapping. No OMOP `concept_id` values are asserted, because none were looked up and verified in Athena.
- **What a real ETL would need:** to map source values to standard concepts and record `*_source_value` / `*_source_concept_id`.

| Prototype table | OMOP CDM table | Notes |
|---|---|---|
| `patients` | `person` | `patient_id` → `person_source_value`; gender and race as `*_source_value`. Age at diagnosis is derived in OMOP from `year_of_birth` and the condition start date. TCGA does not distribute either; the source gives an age top-coded at 90. |
| `patients.tissue_source_site` | `care_site` | TSS code → `care_site_source_value` |
| `clinical.histological_type` | `condition_occurrence` | Breast carcinoma diagnosis with histology. In OMOP Oncology it would be an ICD-O-3 histology/topography condition concept. |
| `clinical.ajcc_stage`, `stage_group` | `measurement` | Stage group as a measurement with a value concept. In the OMOP Oncology extension it is modifier-of-condition. |
| `clinical.er_status`, `pr_status`, `her2_*` | `measurement` | Biomarker test with a categorical result (`value_as_concept_id`). IHC and ISH are separate measurements. |
| `clinical.pam50_subtype` | `measurement` | Derived genomic classification; source value kept. |
| `clinical.os_event`, `vital_status` | `death` | A death record exists when `os_event = 1`. TCGA gives days to death, not dates. |
| `clinical.pfi_*` | `episode` / `episode_event` (Oncology extension) | Disease-progression episode. CDR PFI is a derived endpoint, not a raw event. |
| `rna_samples` | `specimen` | Sample barcode → `specimen_source_id`; sample type → specimen type. |
| `gene_expression` | `measurement` | Gene symbol → `measurement_source_value`; log2(RSEM+1) → `value_as_number`. A real ETL would need a unit/method concept for RNA-seq. |
| `pathology_reports` | `note` | `report_text` → `note_text`. This is where note de-identification and access control normally attach. |
| `slide_images` | `image_occurrence` (OHDSI medical-imaging extension, not core CDM) | Modality, file reference and linked specimen. |
| `data_availability` | n/a | A discovery convenience table. In an OMOP shop it would be a cohort/feasibility view computed from the tables above. |

**Why naming only.** Shared data models are a common requirement for research data platforms. A crosswalk shows that the design is standards-aware. Asserting unverified concept IDs would put the "do not fabricate" rule at risk for little demonstration value. Possible next step: map the ER/PR/HER2 categorical results and histology to standard concepts using a pinned Athena vocabulary download, and add a test that checks every mapped ID against that download.
