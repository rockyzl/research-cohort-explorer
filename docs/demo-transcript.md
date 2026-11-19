# Demo transcript

Generated 2026-11-19 13:51 UTC by `scripts/demo.py` against the in-process app. NL-to-SQL backend: **Azure OpenAI deployment 'gpt-4.1' (api 2024-10-21, temperature 0, prompt v1)**. Every value below is the live API response.

## 1. Catalog search: "receptor"
| table.column | tier | description |
|---|---|---|
| clinical.er_status | public | Estrogen receptor status of the primary tumor (IHC). |
| clinical.pr_status | public | Progesterone receptor status of the primary tumor (IHC). |
| clinical.her2_ihc_status | public | HER2 immunohistochemistry status only. |
| clinical.ajcc_stage | public | AJCC pathologic tumor stage as recorded (e.g. 'Stage IIA'); NULL if '[Not Available]'. |
| clinical.her2_ish_status | public | HER2 in situ hybridization (FISH/CISH) outcome. |
| clinical.her2_status | public | Combined HER2 call: Positive if IHC or ISH Positive; else Negative if IHC or ISH Negative; else Equivocal; else Indeterminate; NULL if neither test recorded. Use this column for 'HER2-positive/negative' questions. |

## 2. Provenance
| source | file | license |
|---|---|---|
| cdr | TCGA-CDR-SupplementalTableS1.xlsx | NCI Genomic Data Commons open-access PanCanAtlas publication supplement; TCGA open-access data (NIH TCGA publication guidelines, no access restrictions) |
| rna | HiSeqV2.gz | UCSC Xena TCGA hub; dataset metadata security=public, redistribution=true; underlying TCGA open-access Level 3 data |
| rna_metadata | HiSeqV2.json | UCSC Xena dataset metadata |
| reports | TCGA_Reports.csv.zip | CC BY 4.0 (Mendeley Data DOI 10.17632/hyg5xkznpx.1); code repository jkefeli/tcga-path-reports is MIT |
| xena_clinical | BRCA_clinicalMatrix | UCSC Xena TCGA hub phenotype matrix (TCGA open-access clinical data, Xena version 2019-12-06) |
| xena_clinical_metadata | BRCA_clinicalMatrix.json | UCSC Xena dataset metadata |
| gdc_slides | gdc_brca_slide_files.json | NCI Genomic Data Commons open-access file metadata (slide images themselves NOT downloaded) |

Ingest row counts: `{"patients": 1092, "clinical": 1092, "rna_samples": 1209, "gene_expression": 12090, "pathology_reports": 1029, "slide_images": 3091, "data_availability": 1092}`

## 3. Cohort builder: ER-positive, HER2-negative, with RNA + report + diagnostic slide; by PAM50
Filters: `{"er_status": ["Positive"], "her2_status": ["Negative"], "require": ["has_rna_tumor", "has_pathology_report", "has_diagnostic_slide"]}`

| patients | with_survival | with_receptor_status | with_rna_tumor | with_pathology_report | with_diagnostic_slide | with_rna_report_slide |
|---|---|---|---|---|---|---|
| 539 | suppressed | 539 | 539 | 539 | 539 | 539 |

Rows: 1; suppressed cells: 1; export: allowed (aggregate result with small-cell suppression).

Executed SQL:
```sql
SELECT COUNT(*) AS patients, SUM(a.has_survival) AS with_survival, SUM(a.has_receptor_status) AS with_receptor_status, SUM(a.has_rna_tumor) AS with_rna_tumor, SUM(a.has_pathology_report) AS with_pathology_report, SUM(a.has_diagnostic_slide) AS with_diagnostic_slide, SUM(a.has_rna_tumor * a.has_pathology_report * a.has_diagnostic_slide) AS with_rna_report_slide, COUNT(DISTINCT p.patient_id) AS __cell_n FROM patients AS p JOIN clinical AS c ON c.patient_id = p.patient_id JOIN data_availability AS a ON a.patient_id = p.patient_id WHERE c.er_status IN ('Positive') AND c.her2_status IN ('Negative') AND a.has_rna_tumor = 1 AND a.has_pathology_report = 1 AND a.has_diagnostic_slide = 1
```
Checks passed: parsed as one SQLite statement; SELECT-only, no DDL/DML/PRAGMA/ATTACH; tables in catalog allow-list: ['clinical', 'data_availability', 'patients']; functions in allow-list; 10 column(s) resolved against catalog and readable by role 'researcher'; aggregate-only output with no identifier columns (researcher tier); executed on read-only connection with engine authorizer (role 'researcher'); small-cell suppression blanked 1 cell(s) (n<11)

Breakdown:

| pam50_subtype | patients |
|---|---|
| LumA | 285 |
| NULL | 118 |
| LumB | 111 |
| Normal | 13 |
| Basal | <11 |
| Her2 | <11 |

Rows: 6; suppressed cells: 2; export: allowed (aggregate result with small-cell suppression).

Executed SQL:
```sql
SELECT c.pam50_subtype AS pam50_subtype, COUNT(*) AS patients, COUNT(DISTINCT p.patient_id) AS __cell_n FROM patients AS p JOIN clinical AS c ON c.patient_id = p.patient_id JOIN data_availability AS a ON a.patient_id = p.patient_id WHERE c.er_status IN ('Positive') AND c.her2_status IN ('Negative') AND a.has_rna_tumor = 1 AND a.has_pathology_report = 1 AND a.has_diagnostic_slide = 1 GROUP BY c.pam50_subtype ORDER BY patients DESC
```
Checks passed: parsed as one SQLite statement; SELECT-only, no DDL/DML/PRAGMA/ATTACH; tables in catalog allow-list: ['clinical', 'data_availability', 'patients']; functions in allow-list; 9 column(s) resolved against catalog and readable by role 'researcher'; aggregate-only output with no identifier columns (researcher tier); executed on read-only connection with engine authorizer (role 'researcher'); small-cell suppression blanked 2 cell(s) (n<11)

## 4. Small cohort: stage IV + triple-negative (small-cell suppression)
| patients | with_survival | with_receptor_status | with_rna_tumor | with_pathology_report | with_diagnostic_slide | with_rna_report_slide |
|---|---|---|---|---|---|---|
| <11 | <11 | <11 | <11 | <11 | <11 | <11 |

Rows: 1; suppressed cells: 7; export: allowed (aggregate result with small-cell suppression).

Executed SQL:
```sql
SELECT COUNT(*) AS patients, SUM(a.has_survival) AS with_survival, SUM(a.has_receptor_status) AS with_receptor_status, SUM(a.has_rna_tumor) AS with_rna_tumor, SUM(a.has_pathology_report) AS with_pathology_report, SUM(a.has_diagnostic_slide) AS with_diagnostic_slide, SUM(a.has_rna_tumor * a.has_pathology_report * a.has_diagnostic_slide) AS with_rna_report_slide, COUNT(DISTINCT p.patient_id) AS __cell_n FROM patients AS p JOIN clinical AS c ON c.patient_id = p.patient_id JOIN data_availability AS a ON a.patient_id = p.patient_id WHERE c.stage_group IN ('IV') AND c.triple_negative = 1
```
Checks passed: parsed as one SQLite statement; SELECT-only, no DDL/DML/PRAGMA/ATTACH; tables in catalog allow-list: ['clinical', 'data_availability', 'patients']; functions in allow-list; 10 column(s) resolved against catalog and readable by role 'researcher'; aggregate-only output with no identifier columns (researcher tier); executed on read-only connection with engine authorizer (role 'researcher'); small-cell suppression blanked 7 cell(s) (n<11)

## 5. Natural-language questions
### Ask (researcher): "How many triple-negative patients have a primary-tumor RNA-seq sample and a diagnostic slide?"
- attempt 1: `SELECT COUNT(DISTINCT da.patient_id) AS n_patients FROM data_availability da JOIN clinical c ON da.patient_id = c.patient_id WHERE c.triple_negative = 1 AND da.has_rna_tumor = 1 AND da.has_diagnostic_slide = 1;`
- outcome: **valid**

| n_patients |
|---|
| 150 |

Rows: 1; suppressed cells: 0; export: allowed (aggregate result with small-cell suppression).

Executed SQL:
```sql
SELECT COUNT(DISTINCT da.patient_id) AS n_patients, COUNT(DISTINCT da.patient_id) AS __cell_n FROM data_availability AS da JOIN clinical AS c ON da.patient_id = c.patient_id WHERE c.triple_negative = 1 AND da.has_rna_tumor = 1 AND da.has_diagnostic_slide = 1
```
Checks passed: parsed as one SQLite statement; SELECT-only, no DDL/DML/PRAGMA/ATTACH; tables in catalog allow-list: ['clinical', 'data_availability']; functions in allow-list; 5 column(s) resolved against catalog and readable by role 'researcher'; aggregate-only output with no identifier columns (researcher tier); executed on read-only connection with engine authorizer (role 'researcher')

### Ask (researcher): "List the ER-positive patients diagnosed under 35 with their ages."
- attempt 1: `SELECT patient_id, age_at_diagnosis FROM patients JOIN clinical USING(patient_id) WHERE er_status = 'Positive' AND age_at_diagnosis < 35`
  - guard rejected: [aggregate_only] role 'researcher' may only run aggregate queries (GROUP BY or COUNT/AVG/MIN/MAX/SUM in the outermost SELECT); row-level results require role 'approved'
- attempt 2: model refused: Row-level results are not permitted for the researcher role; only aggregate queries are allowed.
- outcome: **model_refused**

### Ask (researcher): "Show me the pathology report for TCGA-A1-A0SB."
- attempt 1: model refused: Report text is restricted and not available in the schema.
- outcome: **model_refused**

### Ask (approved): "How many pathology reports mention lobular carcinoma?"
- attempt 1: `SELECT COUNT(*) AS n_reports FROM pathology_reports WHERE report_text LIKE '%lobular carcinoma%';`
- outcome: **valid**

| n_reports |
|---|
| 281 |

Rows: 1; suppressed cells: 0; export: blocked (query reads a free-text column; free text is never exported).

Executed SQL:
```sql
SELECT COUNT(*) AS n_reports, COUNT(DISTINCT pathology_reports.patient_id) AS __cell_n FROM pathology_reports AS pathology_reports WHERE pathology_reports.report_text LIKE '%lobular carcinoma%'
```
Checks passed: parsed as one SQLite statement; SELECT-only, no DDL/DML/PRAGMA/ATTACH; tables in catalog allow-list: ['pathology_reports']; functions in allow-list; 1 column(s) resolved against catalog and readable by role 'approved'; executed on read-only connection with engine authorizer (role 'approved')

## 6. Direct SQL that the guard must stop
`SELECT name, sql FROM sqlite_master`

**Rejected** at `table_allowlist`: table 'sqlite_master' is not in the catalog

`SELECT patient_id, report_text FROM pathology_reports`

**Rejected** at `access_tier`: column pathology_reports.report_text is restricted; role 'researcher' is not authorized

`DELETE FROM patients`

**Rejected** at `read_only`: only SELECT queries are allowed, got Delete

## 7. Export (aggregate CSV with suppression)
```
race,patients
,95
AMERICAN INDIAN OR ALASKA NATIVE,<11
ASIAN,suppressed
BLACK OR AFRICAN AMERICAN,183
WHITE,752
# small-cell suppression: values 1-10 shown as <11; suppressed cells: 2
# sql: SELECT patients.race AS race, COUNT(*) AS patients, COUNT(DISTINCT patients.patient_id) AS __cell_n FROM patients AS patients GROUP BY patients.race
```
Export of a query that reads free text (role approved): HTTP 403, `query reads a free-text column; free text is never exported`
