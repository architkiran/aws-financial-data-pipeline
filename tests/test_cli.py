from pathlib import Path

from finpipe.cli import main

SQL_DIR = str(Path(__file__).resolve().parents[1] / "sql" / "analytics")


def test_backfill_summary_and_query_commands(tmp_path, capsys):
    lake, landing = str(tmp_path / "lake"), str(tmp_path / "landing")
    main(["--lake", lake, "backfill", "--start", "2026-10-01", "--end", "2026-10-02", "--rows", "500", "--landing", landing])
    out = capsys.readouterr().out
    assert "2026-10-01" in out and "2026-10-02" in out and "quarantined=" in out

    main(["--lake", lake, "summary"])
    out = capsys.readouterr().out
    assert out.count("SUCCEEDED") == 2

    main(["--lake", lake, "query", "01", "--sql-dir", SQL_DIR])
    assert "txn_date" in capsys.readouterr().out


def test_generate_ingest_run_commands(tmp_path, capsys):
    lake, landing = str(tmp_path / "lake"), str(tmp_path / "landing")
    main(["--lake", lake, "generate", "--start", "2026-10-05", "--rows", "300", "--landing", landing])
    path = str(Path(landing) / "transactions_2026-10-05.csv")
    main(["--lake", lake, "ingest", path])
    main(["--lake", lake, "run", "--date", "2026-10-05"])
    out = capsys.readouterr().out
    assert "ingested:" in out and "status=SUCCEEDED" in out and "[ok] amount_control_total" in out
