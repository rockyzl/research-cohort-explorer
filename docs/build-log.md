# Build log

Chronological record kept *while* building (not reconstructed afterwards). Each entry: what was
attempted, what went wrong, how it was caught. `docs/ai-assisted-engineering.md` summarizes this log.

## 2026-11-19

- **Xena redirect stub.** The first ad-hoc `curl -o` of `BRCA_clinicalMatrix` returned HTTP 302 and
  saved a 110-byte HTML "302 Found" page under the data file name, with exit code 0. Caught by
  inspecting the header line (it was `<html>`), not by the command status. Fix: `scripts/fetch.py`
  follows redirects and refuses `text/html` responses.
- **Reused data verified by hash.** The four files copied from the sibling `tcga-multimodal-text-lab`
  are checked against that lab's recorded SHA-256 before use; `fetch.py` stops on mismatch.
- **Patient universe redefined.** The sibling lab's `clinical.csv` (1078 / 1018 patients) applies
  survival-analysis filters (PFI.time > 0, three-modality join) and a dev/test split. Those are
  wrong for a discovery tool, whose job is to show *who has what*. Ingest rebuilds the universe
  from raw CDR rows instead.
- **Catalog value check caught an incomplete enum.** The hand-written `allowed_values` for
  `clinical.ajcc_stage` omitted `Stage III` (2 patients). Ingest validates every loaded column
  against the catalog and stopped with `values outside catalog allowed_values: {'Stage III'}`.
  Fixed in the catalog; the check stays because the NL-to-SQL prompt shows these enums to the model,
  so a missing value would silently steer generated filters.
