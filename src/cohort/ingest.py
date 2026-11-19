"""Build data/cohort.sqlite from the raw public files, with tables created from the catalog.

Run: python -m cohort.ingest
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import zipfile
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from cohort.catalog import DB_PATH, ROOT, load_catalog

RAW = ROOT / "data" / "raw"
GENE_PANEL = ["ESR1", "PGR", "ERBB2", "MKI67", "GATA3", "FOXA1", "KRT5", "EGFR", "AURKA", "CCNB1"]
SAMPLE_TYPES = {"01": "Primary Tumor", "06": "Metastatic", "11": "Solid Tissue Normal"}
NA_TOKENS = {"", "[Not Available]", "[Not Evaluated]", "[Unknown]", "[Not Applicable]", "#N/A"}


def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s in NA_TOKENS else s


def stage_group(stage: str | None) -> str | None:
    if not stage or not stage.startswith("Stage ") or stage == "Stage X":
        return None
    roman = stage.removeprefix("Stage ").rstrip("ABC")
    return roman if roman in {"I", "II", "III", "IV"} else None


def menopause(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    for key in ("Pre", "Peri", "Post", "Indeterminate"):
        if v.startswith(key):
            return key
    raise ValueError(f"unexpected menopause value {v!r}")


def her2_combined(ihc: str | None, ish: str | None) -> str | None:
    if "Positive" in (ihc, ish):
        return "Positive"
    if "Negative" in (ihc, ish):
        return "Negative"
    if "Equivocal" in (ihc, ish):
        return "Equivocal"
    if "Indeterminate" in (ihc, ish):
        return "Indeterminate"
    return None


def triple_negative(er, pr, her2) -> int | None:
    vals = (er, pr, her2)
    if "Positive" in vals:
        return 0
    if all(v == "Negative" for v in vals):
        return 1
    return None


def _int(v):
    return None if v is None or pd.isna(v) else int(round(float(v)))


def load_cdr() -> pd.DataFrame:
    cdr = pd.read_excel(RAW / "TCGA-CDR-SupplementalTableS1.xlsx", sheet_name="TCGA-CDR")
    cdr = cdr[cdr["type"] == "BRCA"]
    return cdr


def build_frames(stats: dict) -> dict[str, pd.DataFrame]:
    cdr = load_cdr()
    stats["cdr_brca_rows"] = len(cdr)
    stats["cdr_redacted"] = int((cdr["Redaction"] == "Redacted").sum())
    cdr = cdr[cdr["Redaction"] != "Redacted"].copy()
    universe = set(cdr["bcr_patient_barcode"])
    stats["universe_patients"] = len(universe)

    patients = pd.DataFrame({
        "patient_id": cdr["bcr_patient_barcode"],
        "gender": cdr["gender"].map(_clean),
        "race": cdr["race"].map(_clean),
        "age_at_diagnosis": cdr["age_at_initial_pathologic_diagnosis"].map(_int),
        "menopause_status": cdr["menopause_status"].map(menopause),
        "tissue_source_site": cdr["bcr_patient_barcode"].str[5:7],
    })

    # --- receptor status / PAM50 from the sample-level Xena clinical matrix -------------------
    xm = pd.read_csv(RAW / "BRCA_clinicalMatrix", sep="\t", dtype=str, keep_default_na=False)
    fields = {
        "er_status": "breast_carcinoma_estrogen_receptor_status",
        "pr_status": "breast_carcinoma_progesterone_receptor_status",
        "her2_ihc_status": "lab_proc_her2_neu_immunohistochemistry_receptor_status",
        "her2_ish_status": "lab_procedure_her2_neu_in_situ_hybrid_outcome_type",
    }
    receptor, conflicts = {}, Counter()
    for pid, grp in xm.groupby("_PATIENT"):
        rec = {}
        for out, col in fields.items():
            vals = {v for v in grp[col].map(_clean) if v is not None}
            if len(vals) > 1:
                conflicts[out] += 1
                rec[out] = None
            else:
                rec[out] = vals.pop() if vals else None
        prim = grp[grp["sampleID"].str[13:15] == "01"]
        pam = {v for v in prim["PAM50Call_RNAseq"].map(_clean) if v is not None}
        if len(pam) > 1:
            conflicts["pam50_subtype"] += 1
        rec["pam50_subtype"] = pam.pop() if len(pam) == 1 else None
        receptor[pid] = rec
    stats["xena_matrix_patients"] = len(receptor)
    stats["xena_matrix_patients_not_in_universe"] = len(set(receptor) - universe)
    stats["receptor_conflicts_set_null"] = dict(conflicts)

    rows = []
    for _, r in cdr.iterrows():
        pid = r["bcr_patient_barcode"]
        rec = receptor.get(pid, {})
        stage = _clean(r["ajcc_pathologic_tumor_stage"])
        her2 = her2_combined(rec.get("her2_ihc_status"), rec.get("her2_ish_status"))
        rows.append({
            "patient_id": pid,
            "ajcc_stage": stage,
            "stage_group": stage_group(stage),
            "histological_type": _clean(r["histological_type"]),
            "er_status": rec.get("er_status"),
            "pr_status": rec.get("pr_status"),
            "her2_ihc_status": rec.get("her2_ihc_status"),
            "her2_ish_status": rec.get("her2_ish_status"),
            "her2_status": her2,
            "triple_negative": triple_negative(rec.get("er_status"), rec.get("pr_status"), her2),
            "pam50_subtype": rec.get("pam50_subtype"),
            "vital_status": _clean(r["vital_status"]),
            "os_event": _int(r["OS"]),
            "os_days": _int(r["OS.time"]),
            "pfi_event": _int(r["PFI"]),
            "pfi_days": _int(r["PFI.time"]),
        })
    clinical = pd.DataFrame(rows)

    # --- RNA: stream the gzip matrix, keep only panel genes ------------------------------------
    expr_rows, samples = [], []
    with gzip.open(RAW / "HiSeqV2.gz", "rt") as f:
        header = f.readline().rstrip("\n").split("\t")[1:]
        keep_idx = [i for i, s in enumerate(header) if s[:12] in universe]
        samples = [header[i] for i in keep_idx]
        stats["rna_samples_total"] = len(header)
        stats["rna_samples_not_in_universe"] = len(header) - len(samples)
        found = set()
        for line in f:
            gene, rest = line.split("\t", 1)
            if gene not in GENE_PANEL:
                continue
            found.add(gene)
            vals = rest.rstrip("\n").split("\t")
            for i in keep_idx:
                expr_rows.append({"sample_id": header[i], "patient_id": header[i][:12], "gene_symbol": gene,
                                  "log2_expr": float(vals[i])})
    missing = set(GENE_PANEL) - found
    if missing:
        raise SystemExit(f"panel genes missing from HiSeqV2: {missing}")
    rna_samples = pd.DataFrame({"sample_id": samples, "patient_id": [s[:12] for s in samples],
                                "sample_type_code": [s[13:15] for s in samples],
                                "sample_type": [SAMPLE_TYPES[s[13:15]] for s in samples]})
    gene_expression = pd.DataFrame(expr_rows)

    # --- pathology reports ----------------------------------------------------------------------
    with zipfile.ZipFile(RAW / "TCGA_Reports.csv.zip") as z:
        rep = pd.read_csv(z.open("TCGA_Reports.csv"))
    rep["patient_id"] = rep["patient_filename"].str.split(".").str[0]
    rep["report_id"] = rep["patient_filename"].str.split(".", n=1).str[1]
    stats["reports_total_all_cancers"] = len(rep)
    rep = rep[rep["patient_id"].isin(universe)]
    if rep["patient_id"].duplicated().any():
        raise SystemExit("more than one report per patient; catalog says 1:1")
    pathology_reports = pd.DataFrame({"report_id": rep["report_id"], "patient_id": rep["patient_id"],
                                      "n_chars": rep["text"].str.len(), "report_text": rep["text"]})

    # --- slide image metadata ---------------------------------------------------------------------
    hits = json.loads((RAW / "gdc_brca_slide_files.json").read_text())["data"]["hits"]
    srows = []
    for h in hits:
        if len(h["cases"]) != 1 or len(h["cases"][0]["samples"]) != 1:
            raise SystemExit(f"slide {h['file_id']} not linked to exactly one case/sample")
        case, sample = h["cases"][0], h["cases"][0]["samples"][0]
        srows.append({"file_id": h["file_id"], "patient_id": case["submitter_id"], "sample_id": sample["submitter_id"],
                      "sample_type": sample["sample_type"], "slide_type": h["experimental_strategy"],
                      "data_format": h["data_format"], "file_size_bytes": int(h["file_size"]),
                      "gdc_access": h["access"]})
    slides = pd.DataFrame(srows)
    stats["gdc_slide_files"] = len(slides)
    stats["gdc_slide_files_not_in_universe"] = int((~slides["patient_id"].isin(universe)).sum())
    slide_images = slides[slides["patient_id"].isin(universe)].reset_index(drop=True)

    # --- availability -----------------------------------------------------------------------------
    av = pd.DataFrame({"patient_id": sorted(universe)})
    c = clinical.set_index("patient_id")
    av["has_survival"] = av["patient_id"].map(
        lambda p: int(pd.notna(c.at[p, "os_days"]) and pd.notna(c.at[p, "pfi_days"])
                      and c.at[p, "os_days"] > 0 and c.at[p, "pfi_days"] > 0))
    av["has_receptor_status"] = av["patient_id"].map(
        lambda p: int(all(pd.notna(c.at[p, k]) for k in ("er_status", "pr_status", "her2_status"))))
    tumor = set(rna_samples.loc[rna_samples["sample_type_code"] == "01", "patient_id"])
    normal = set(rna_samples.loc[rna_samples["sample_type_code"] == "11", "patient_id"])
    av["has_rna_tumor"] = av["patient_id"].isin(tumor).astype(int)
    av["has_rna_normal"] = av["patient_id"].isin(normal).astype(int)
    av["has_pathology_report"] = av["patient_id"].isin(set(pathology_reports["patient_id"])).astype(int)
    counts = slide_images.groupby(["patient_id", "slide_type"]).size().unstack(fill_value=0)
    av["n_diagnostic_slides"] = av["patient_id"].map(counts.get("Diagnostic Slide", {})).fillna(0).astype(int)
    av["n_tissue_slides"] = av["patient_id"].map(counts.get("Tissue Slide", {})).fillna(0).astype(int)
    av["has_diagnostic_slide"] = (av["n_diagnostic_slides"] > 0).astype(int)

    return {"patients": patients, "clinical": clinical, "rna_samples": rna_samples,
            "gene_expression": gene_expression, "pathology_reports": pathology_reports,
            "slide_images": slide_images, "data_availability": av}


def main() -> None:
    cat = load_catalog()
    stats: dict = {"built_at": datetime.now(timezone.utc).isoformat()}
    frames = build_frames(stats)
    if set(frames) != set(cat.tables):
        raise SystemExit(f"tables {set(frames)} != catalog {set(cat.tables)}")
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    for name, df in frames.items():
        expected = list(cat.columns[name])
        if list(df.columns) != expected:
            raise SystemExit(f"{name}: columns {list(df.columns)} != catalog {expected}")
        for col in cat.columns[name].values():
            allowed = col.allowed_values
            if allowed:
                bad = set(df[col.name].dropna().unique()) - set(allowed)
                if bad:
                    raise SystemExit(f"{name}.{col.name}: values outside catalog allowed_values: {bad}")
        con.execute(cat.ddl(name))
        df = df.astype(object).where(pd.notna(df), None)
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(expected))})',
                        df.itertuples(index=False, name=None))
        stats.setdefault("row_counts", {})[name] = len(df)
    con.execute("CREATE INDEX ix_ge_gene ON gene_expression(gene_symbol)")
    con.commit()
    con.close()
    tmp.replace(DB_PATH)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "ingest_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
