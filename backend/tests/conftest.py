"""Prints the engine-vs-Excel comparison table at the end of the test run."""
from __future__ import annotations


def pytest_terminal_summary(terminalreporter):
    if not any("test_step2" in str(r.fspath) for r in terminalreporter.stats.get("passed", [])
               + terminalreporter.stats.get("failed", [])):
        return
    from backend.tests.test_step2_calculator import COMPARED_FIELDS, REFS, run_reference

    tr = terminalreporter
    tr.section("Engine vs. Excel 'חישובים' (engine / excel)")
    short = ["net_today", "net_alt", "diff", "tax_today", "tax_alt",
             "ni+h_today", "ni+h_alt", "elec_gross", "family_share"]
    tr.write_line("ID     " + "".join(f"{h:>15}" for h in short) + "   OK")
    total_ok = 0
    for ref in REFS:
        res = run_reference(ref)
        cells, ok = [], True
        for eng, xl, _ in COMPARED_FIELDS:
            e, x = getattr(res, eng), getattr(ref, xl)
            ok &= e == x
            cells.append(f"{e:>6}/{x:<6.0f}" + ("  " if e == x else " ✗"))
        total_ok += ok
        tr.write_line(f"{ref.employee_id}  " + "".join(f"{c:>15}" for c in cells)
                      + ("   ✓" if ok else "   ✗"))
    tr.write_line(f"{total_ok}/{len(REFS)} employees match the reference sheet on all "
                  f"{len(COMPARED_FIELDS)} columns")
