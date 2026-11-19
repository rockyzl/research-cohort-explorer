"""Independent check of every gold reference answer.

This file deliberately does NOT read data/cohort.sqlite and does NOT import cohort.ingest.
It re-parses the raw source files with pandas, re-derives the few normalized fields it needs
(written separately, in a different style), answers each gold question in pandas, and compares
with the result of the reference SQL. Agreement means two independent implementations agree; it
does not prove both are clinically right, which is why semantic choices are listed in the README.

Run: python gold/verify_pandas.py  -> results/gold_verification.json
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "src"))


def missing(s: pd.Series) -> pd.Series:
    return s.isna() | s.astype(str).str.strip().isin(["", "[Not Available]", "[Not Evaluated]", "[Unknown]"])


def load() -> dict[str, pd.DataFrame]:
    cdr = pd.read_excel(RAW / "TCGA-CDR-SupplementalTableS1.xlsx", sheet_name="TCGA-CDR")
    cdr = cdr.loc[(cdr["type"] == "BRCA") & (cdr["Redaction"].fillna("") != "Redacted")].copy()
    cdr = cdr.rename(columns={"bcr_patient_barcode": "pid"}).set_index("pid")
    df = pd.DataFrame(index=cdr.index)
    df["gender"] = cdr["gender"]
    df["race"] = cdr["race"].mask(missing(cdr["race"]))
    df["age"] = cdr["age_at_initial_pathologic_diagnosis"]
    meno = cdr["menopause_status"].mask(missing(cdr["menopause_status"]))
    df["meno"] = meno.str.extract(r"^(Pre|Peri|Post|Indeterminate)", expand=False)
    df["tss"] = [p.split("-")[1] for p in df.index]
    st = cdr["ajcc_pathologic_tumor_stage"].astype(str)
    df["stage"] = st.str.extract(r"^Stage (IV|III|II|I)[ABC]?$", expand=False)
    df["hist"] = cdr["histological_type"].mask(missing(cdr["histological_type"]))
    df["vital"] = cdr["vital_status"]
    df["pfi"] = cdr["PFI"]
    df["pfi_t"] = pd.to_numeric(cdr["PFI.time"], errors="coerce")
    df["os_t"] = pd.to_numeric(cdr["OS.time"], errors="coerce")

    xm = pd.read_csv(RAW / "BRCA_clinicalMatrix", sep="\t", dtype=str).set_index("sampleID")
    for out, col in [("er", "breast_carcinoma_estrogen_receptor_status"),
                     ("pr", "breast_carcinoma_progesterone_receptor_status"),
                     ("ihc", "lab_proc_her2_neu_immunohistochemistry_receptor_status"),
                     ("ish", "lab_procedure_her2_neu_in_situ_hybrid_outcome_type")]:
        per_patient = xm.groupby("_PATIENT")[col].agg(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
        df[out] = df.index.map(per_patient)
    tumor_rows = xm[xm.index.str.endswith("-01")]
    pam = tumor_rows.groupby("_PATIENT")["PAM50Call_RNAseq"].first()
    df["pam50"] = df.index.map(pam)

    def her2(row):
        tests = [row["ihc"], row["ish"]]
        for verdict in ("Positive", "Negative", "Equivocal", "Indeterminate"):
            if verdict in tests:
                return verdict
        return np.nan
    df["her2"] = df.apply(her2, axis=1)
    statuses = df[["er", "pr", "her2"]]
    df["tn"] = np.where((statuses == "Positive").any(axis=1), 0,
                        np.where((statuses == "Negative").all(axis=1), 1, np.nan))

    with gzip.open(RAW / "HiSeqV2.gz", "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        wanted = {}
        for line in f:
            g = line[: line.index("\t")]
            if g in ("ESR1", "ERBB2", "MKI67"):
                wanted[g] = [float(x) for x in line.rstrip("\n").split("\t")[1:]]
    expr = pd.DataFrame(wanted, index=header[1:])
    expr = expr[expr.index.str[:12].isin(df.index)]
    samples = pd.DataFrame({"sample": expr.index, "pid": expr.index.str[:12], "code": expr.index.str[-2:]})

    with zipfile.ZipFile(RAW / "TCGA_Reports.csv.zip") as z:
        rep = pd.read_csv(io.BytesIO(z.read("TCGA_Reports.csv")))
    rep["pid"] = rep["patient_filename"].str[:12]
    rep = rep[rep["pid"].isin(df.index)]
    rep["n"] = rep["text"].str.len()

    hits = json.loads((RAW / "gdc_brca_slide_files.json").read_text())["data"]["hits"]
    slides = pd.DataFrame([{"pid": h["cases"][0]["submitter_id"], "kind": h["experimental_strategy"],
                            "size": h["file_size"]} for h in hits])
    slides = slides[slides["pid"].isin(df.index)]

    n_diag = slides[slides["kind"] == "Diagnostic Slide"].groupby("pid").size()
    n_tis = slides[slides["kind"] == "Tissue Slide"].groupby("pid").size()
    df["n_diag"] = df.index.map(n_diag).fillna(0)
    df["n_tis"] = df.index.map(n_tis).fillna(0)
    df["report"] = df.index.isin(rep["pid"])
    df["rna01"] = df.index.isin(samples.loc[samples["code"] == "01", "pid"])
    df["rna11"] = df.index.isin(samples.loc[samples["code"] == "11", "pid"])
    df["surv"] = (df["os_t"] > 0) & (df["pfi_t"] > 0)
    return {"p": df, "expr": expr, "samples": samples, "rep": rep, "slides": slides}


def tumor_mean(d, gene, mask_patients):
    e = d["expr"]
    e = e[e.index.str.endswith("-01") & e.index.str[:12].isin(mask_patients)]
    return round(float(e[gene].mean()), 2)


def answers(d) -> dict[str, list[tuple]]:
    p = d["p"]
    vc = lambda s: sorted((k, int(v)) for k, v in s.value_counts().items())  # noqa: E731
    out = {
        "D01": [(len(p),)],
        "D02": [(int((p.gender == "MALE").sum()),)],
        "D03": [(int((p.er == "Positive").sum()),)],
        "D04": vc(p.pam50.dropna()),
        "D05": [(round(float(p.loc[p.tn == 1, "age"].mean()), 1),)],
        "D06": [(int((p.report & p.rna01 & (p.n_diag > 0)).sum()),)],
        "D07": [(int((d["slides"].kind == "Diagnostic Slide").sum()),)],
        "D08": [(int(((p.stage == "III") & (p.her2 == "Positive")).sum()),)],
        "D09": [(tumor_mean(d, "ESR1", p.index[p.er == "Negative"]),)],
        "D10": [(k, int(v)) for k, v in p.tss.value_counts().head(5).items()],
        "D11": [(int(((p.stage == "IV") & (p.vital == "Dead")).sum()),)],
        "D12": [(int(p.rna11.sum()),)],
        "T01": [(int(((p.gender == "FEMALE") & (p.age <= 40)).sum()),)],
        "T02": [(int((p.pr == "Negative").sum()),)],
        "T03": [(int((p.tn == 1).sum()),)],
        "T04": vc(p.stage.dropna()),
        "T05": [(int(((p["hist"] == "Infiltrating Lobular Carcinoma") & (p.er == "Positive")).sum()),)],
        "T06": sorted((k, round(float(v), 1)) for k, v in p.groupby("meno")["age"].mean().items()),
        "T07": [(int(((p.n_tis > 0) & (p.n_diag == 0)).sum()),)],
        "T08": [(int((p.rna01 & p.rna11).sum()),)],
        "T09": vc(p.loc[p.her2 == "Positive", "pam50"].dropna()),
        "T10": [(int(((p.pam50 == "Basal") & (p.tn == 1)).sum()),)],
        "T11": sorted([("Negative", tumor_mean(d, "ERBB2", p.index[p.her2 == "Negative"])),
                       ("Positive", tumor_mean(d, "ERBB2", p.index[p.her2 == "Positive"]))]),
        "T12": [(int((p.pfi == 1).sum()),)],
        "T13": [(float(round(p.loc[p.pfi == 1, "pfi_t"].mean())),)],
        "T14": [(int(((p.age >= 65) & (p.stage == "I")).sum()),)],
        "T15": [(round(d["slides"].loc[d["slides"].kind == "Diagnostic Slide", "size"].sum() / 1e9, 1),)],
        "T16": [(p["hist"].value_counts().index[0], int(p["hist"].value_counts().iloc[0]))],
        "T17": [(int((d["rep"].n > 5000).sum()),)],
        "T18": [(int((~p.index.isin(d["samples"].pid)).sum()),)],
        "T19": [(int(((p.er == "Positive") & (p.her2 == "Negative") & (p.n_diag > 0) & p.report).sum()),)],
        "T20": [(k, int(v)) for k, v in p.race.value_counts().items()],
        "T21": [(int((p.n_diag > 1).sum()),)],
        "T22": sorted((s, tumor_mean(d, "MKI67", p.index[p.pam50 == s])) for s in p.pam50.dropna().unique()),
        "T23": [(int(p.er.isna().sum()),)],
        "T24": [(int(p.loc[p.stage == "IV", "age"].min()), int(p.loc[p.stage == "IV", "age"].max()))],
        "T25": [(int((d["samples"].code == "06").sum()),)],
        "T26": [(int((p.surv & ~p.report).sum()),)],
        "T27": [(round(100.0 * float((p.loc[p.tn == 1, "n_diag"] > 0).mean()), 1),)],
        "T28": [(int(((p.ihc == "Equivocal") & (p.ish == "Positive")).sum()),)],
        # stress set (gold/stress.yaml), primary reference interpretation
        "S01": [(int(((p.er == "Positive") | (p.pr == "Positive")).sum()),)],
        "S02": [(int((p.pam50 == "LumA").sum()),)],
        "S03": [(int(((p["hist"] == "Infiltrating Ductal Carcinoma") & (p.stage == "II")).sum()),)],
        "S04": [(int(((p.age > 70) & (p.n_tis > 0)).sum()),)],
        "S05": sorted((((k if isinstance(k, str) else None), int(v))
                       for k, v in p.her2.value_counts(dropna=False).items()), key=lambda r: str(r[0])),
        "S06": vc(p.pam50[p.pam50.isin(["Basal", "Her2"])]),
        "S07": [(int(((p.tn == 1) & p.rna01 & p.report & (p.n_diag > 0)).sum()),)],
        "S08": [(float(p.loc[p["hist"] == "Infiltrating Lobular Carcinoma", "age"].mean()),)],
        "S09": [(int((p.rna01 & p.rna11).sum()),)],
        "S10": [(max(p.pam50.dropna().unique(), key=lambda s: tumor_mean(d, "ESR1", p.index[p.pam50 == s])),)],
        "S13": [(float((p.gender == "MALE").mean()),)],
        "S14": [(int(d["slides"].pid.nunique()),)],
        "S15": [(float(p.age.median()),)],
        "S16": [(int(((p.stage == "IV") & (p.vital == "Alive")).sum()),)],
        "S17": [(int(((p.er == "Positive") & (p.age < 35)).sum()),)],
        "S18": sorted((int(k), float(v)) for k, v in p.loc[p.tn.notna()].groupby("tn")["pfi"].mean().items()),
    }
    return out


def main() -> int:
    from cohort.evaluate import results_match  # same comparator the harness uses
    from cohort.service import run_sql_raw
    gold = (yaml.safe_load((ROOT / "gold" / "gold.yaml").read_text())["items"]
            + yaml.safe_load((ROOT / "gold" / "stress.yaml").read_text())["items"])
    d = load()
    pd_ans = answers(d)
    report, bad = [], 0
    for it in gold:
        if it["kind"] not in ("answer", "either"):
            continue
        ref = run_sql_raw(it["sql"]).rows
        exp_rows = pd_ans.get(it["id"])
        ok = exp_rows is not None and results_match(ref, exp_rows, ordered=it.get("ordered", False))
        bad += not ok
        report.append({"id": it["id"], "agree": ok, "reference_sql_result": ref, "pandas_result": exp_rows})
    out = {"n_items": len(report), "n_agree": len(report) - bad, "items": report}
    (ROOT / "results" / "gold_verification.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"pandas cross-check: {out['n_agree']}/{out['n_items']} reference answers agree")
    for r in report:
        if not r["agree"]:
            print("  DISAGREE", r["id"], r["reference_sql_result"], r["pandas_result"])
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
