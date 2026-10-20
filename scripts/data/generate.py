"""Generate all synthetic data for the three demo SMEs.

    python scripts/data/generate.py --seed 42          # everything, < 2 minutes
    python scripts/data/generate.py --only bd_grocer   # one company

Deterministic: same seed -> byte-identical files (see data/synthetic/MANIFEST.sha256).
Outputs per company under data/synthetic/<company>/: truth/, books/, sources/, answer_key.json.
All data is SYNTHETIC. Public anchors (data/anchors/) only shape prices, FX, weather and seasonality.
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import canon  # noqa: E402
import core  # noqa: E402
import key  # noqa: E402
from core import SYN, D0, D1, Trace, month_end, sha256_file, write_csv, write_json  # noqa: E402

COMPANIES = ["bd_grocer", "cn_xborder", "uk_garden"]


def load_module(cid: str):
    if cid == "bd_grocer":
        import bd, bd_plant, bd_export
        return bd, bd_plant, bd_export
    if cid == "cn_xborder":
        import cn, cn_plant, cn_export
        return cn, cn_plant, cn_export
    import uk, uk_plant, uk_export
    return uk, uk_plant, uk_export


def run_company(cid: str, seed: int, A: core.Anchors, root: Path) -> dict:
    t0 = time.time()
    sim_mod, plant_mod, exp_mod = load_module(cid)
    out = root / cid
    if out.exists():
        shutil.rmtree(out)
    sim = sim_mod.build(seed, A)
    plant_mod.plant(sim, seed)
    w, book, P = sim.world, sim.book, sim.problems
    Tt, Tb = canon.Tables(), canon.Tables()
    res = exp_mod.run(sim, seed, A, out, Tt, Tb)
    trace: Trace = res["trace"]
    meta = sim_mod.COMPANY
    for T, ss, sf in ((Tt, "synthetic", "truth"), (Tb, exp_mod.BOOK_SYSTEM, exp_mod.BOOK_FILE)):
        T.add("companies", id=cid, name=meta["name"], country=meta["country"], base_currency=meta["base_currency"], industry=meta["industry"],
              fiscal_year_start_month=meta["fiscal_year_start_month"], founded_on=meta.get("founded_on"), description=meta["description"] + " SYNTHETIC.",
              source_system=ss, source_file=sf, source_row_id=cid)
        canon.add_accounts(T, w, ss, sf)
    canon.add_journal(Tt, w, w.entries, prefix="jl", lineage=lambda e: ("synthetic", "truth", e.id))

    def book_lineage(e):
        rows = [r for r in trace.by_ref.get(e.id, [])]
        if not rows:
            raise RuntimeError(f"book entry {e.id} has no source row ({e.kind})")
        f, r = rows[0]
        return exp_mod.system_for(f), f, r

    canon.add_journal(Tb, w, list(book.sorted_entries()), prefix="bl", lineage=book_lineage)
    c_t = Tt.write(out / "truth")
    c_b = Tb.write(out / "books")
    write_csv(out / "truth" / "source_trace.csv.gz", ["file", "row_id", "ref_kind", "ref_id"], trace.rows, gz=True)
    asofs = key.asof_dates()
    ky = key.build_key(cid=cid, name=meta["name"], base=meta["base_currency"], w=w, book=book, P=P, trace=trace, asofs=asofs, fx_at=exp_mod.fx_at(A),
                       seed=seed, extra=res.get("key_extra", {}), findings=res.get("findings"))
    write_json(out / "answer_key.json", ky)
    g = ky["cash_truth_gap"][D1.isoformat()]
    return dict(cid=cid, secs=round(time.time() - t0, 1), truth_rows=c_t, book_rows=c_b, summary=ky["summary"], gap=g["gap"], gap_pct=g["gap_pct_of_ttm_revenue"],
                gap_1231=ky["cash_truth_gap"]["2025-12-31"]["gap"], gap_pct_1231=ky["cash_truth_gap"]["2025-12-31"]["gap_pct_of_ttm_revenue"], ccy=meta["base_currency"])


def write_manifest(root: Path) -> None:
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.sha256" and p.name != "README.md":
            lines.append(f"{sha256_file(p)}  {p.relative_to(root).as_posix()}")
    (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--only", nargs="*", default=COMPANIES)
    ap.add_argument("--out", default=str(SYN))
    a = ap.parse_args()
    root = Path(a.out)
    t0 = time.time()
    A = core.Anchors()
    results = []
    for cid in a.only:
        r = run_company(cid, a.seed, A, root)
        results.append(r)
        s = r["summary"]
        print(f"[{cid}] {r['secs']}s  revenue/yr {s['revenue_by_year']}")
        print(f"        planted: {s['planted_problem_total']} {s['planted_problem_counts']}  noise={s['honest_noise_count']}")
        print(f"        Cash Truth gap 2026-09-30 = {r['gap']} {r['ccy']} ({r['gap_pct']}% of trailing-12m revenue); 2025-12-31 = {r['gap_1231']} ({r['gap_pct_1231']}%)")
    if set(a.only) == set(COMPANIES):
        write_manifest(root)
    print(f"done in {round(time.time() - t0, 1)}s, seed={a.seed}")


if __name__ == "__main__":
    main()
