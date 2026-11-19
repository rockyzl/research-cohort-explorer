"""Collect the public TCGA-BRCA source files and record provenance in data/manifest.json.

Files already downloaded by the sibling tcga-multimodal-text-lab are copied (never modified there)
and checked against that lab's recorded SHA-256. Two small sources are downloaded fresh:
the UCSC Xena BRCA clinical matrix (receptor status, PAM50) and GDC slide-image file METADATA
(no images are downloaded).
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
LAB = ROOT.parent / "tcga-multimodal-text-lab"

GDC_SLIDE_FILTER = {
    "filters": {
        "op": "and",
        "content": [
            {"op": "in", "content": {"field": "cases.project.project_id", "value": ["TCGA-BRCA"]}},
            {"op": "in", "content": {"field": "data_type", "value": ["Slide Image"]}},
        ],
    },
    "fields": "file_id,file_name,file_size,data_format,data_type,experimental_strategy,access,"
    "cases.submitter_id,cases.samples.sample_type,cases.samples.submitter_id,md5sum,updated_datetime",
    "format": "JSON",
    "size": "5000",
}

COPIED = {
    # key in lab manifest -> local file
    "cdr": "TCGA-CDR-SupplementalTableS1.xlsx",
    "rna": "HiSeqV2.gz",
    "rna_metadata": "HiSeqV2.json",
    "reports": "TCGA_Reports.csv.zip",
}

DOWNLOADED = {
    "xena_clinical": {
        "file": "BRCA_clinicalMatrix",
        "url": "https://tcga.xenahubs.net/download/TCGA.BRCA.sampleMap/BRCA_clinicalMatrix",
        "license": "UCSC Xena TCGA hub phenotype matrix (TCGA open-access clinical data, Xena version 2019-12-06)",
        "citation": "Goldman MJ, et al. Visualizing and interpreting cancer genomics data via the Xena platform. Nat Biotechnol 2020.",
    },
    "xena_clinical_metadata": {
        "file": "BRCA_clinicalMatrix.json",
        "url": "https://tcga.xenahubs.net/download/TCGA.BRCA.sampleMap/BRCA_clinicalMatrix.json",
        "license": "UCSC Xena dataset metadata",
        "citation": "UCSC Xena",
    },
}

GDC_SLIDES = {
    "file": "gdc_brca_slide_files.json",
    "url": "https://api.gdc.cancer.gov/files (POST)",
    "request_body": GDC_SLIDE_FILTER,
    "license": "NCI Genomic Data Commons open-access file metadata (slide images themselves NOT downloaded)",
    "citation": "Grossman RL, et al. Toward a shared vision for cancer genomic data. NEJM 2016 (NCI GDC).",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def download(url: str, dest: Path, **kw) -> None:
    # allow_redirects matters: the Xena hub answers with a 302 to S3; an earlier ad-hoc curl
    # without -L silently saved the 110-byte HTML redirect page (see docs/build-log.md).
    r = requests.request(kw.pop("method", "GET"), url, timeout=120, allow_redirects=True, **kw)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("text/html"):
        raise RuntimeError(f"{url} returned HTML, not data")
    dest.write_bytes(r.content)


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    lab_manifest = json.loads((LAB / "data" / "manifest.json").read_text())
    sources = {}
    for key, fname in COPIED.items():
        lab_src = lab_manifest["sources"][key]
        dest = RAW / fname
        if not dest.exists():
            src = LAB / "data" / "raw" / fname
            if src.exists():
                shutil.copyfile(src, dest)
                how = f"copied from sibling repo tcga-multimodal-text-lab/data/raw/{fname}"
            else:
                download(lab_src["url"], dest)
                how = "downloaded"
        else:
            how = "already present"
        digest = sha256(dest)
        if digest != lab_src["sha256"]:
            raise SystemExit(f"SHA-256 mismatch for {fname}: {digest} != {lab_src['sha256']}")
        sources[key] = {**{k: lab_src[k] for k in ("url", "license", "citation", "retrieved_at")},
                        "file": fname, "sha256": digest, "bytes": dest.stat().st_size, "obtained": how}

    for key, spec in DOWNLOADED.items():
        dest = RAW / spec["file"]
        if not dest.exists() or dest.stat().st_size < 1000 and spec["file"].endswith("Matrix"):
            download(spec["url"], dest)
        sources[key] = {**spec, "retrieved_at": now(), "sha256": sha256(dest), "bytes": dest.stat().st_size}

    dest = RAW / GDC_SLIDES["file"]
    if not dest.exists():
        download("https://api.gdc.cancer.gov/files", dest, method="POST", json=GDC_SLIDE_FILTER)
    hits = json.loads(dest.read_text())["data"]["hits"]
    sources["gdc_slides"] = {**GDC_SLIDES, "retrieved_at": now(), "sha256": sha256(dest),
                             "bytes": dest.stat().st_size, "n_files": len(hits)}

    manifest_path = ROOT / "data" / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    # keep first retrieval timestamps stable across re-runs when the file hash is unchanged
    for k, v in sources.items():
        prev = old.get("sources", {}).get(k)
        if prev and prev.get("sha256") == v["sha256"]:
            v["retrieved_at"] = prev["retrieved_at"]
    manifest = {**old, "sources": sources, "fetched_at": old.get("fetched_at", now())}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {manifest_path} with {len(sources)} sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
