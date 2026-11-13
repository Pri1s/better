from __future__ import annotations

import pytest
import sourcefiles

from better import cli, config, db
from better.nba import load


def test_data_dir_can_be_moved(data_dir):
    assert config.db_path() == data_dir / "better.duckdb"
    assert config.raw_dir() == data_dir / "raw"


def test_status_on_an_empty_database(data_dir, capsys):
    assert cli.main(["nba", "status"]) == 0
    out = capsys.readouterr().out
    assert "Nothing is loaded yet" in out
    assert "better nba sync --from 2019" in out
    assert (data_dir / "better.duckdb").exists()


def test_status_lists_loaded_seasons(data_dir, source_files, capsys):
    con = db.connect()
    load.load_season(con, sourcefiles.SEASON, source_files)
    con.close()

    assert cli.main(["nba", "status"]) == 0
    lines = capsys.readouterr().out.splitlines()
    header, season = lines[1].split(), lines[2].split()
    assert header[:2] == ["season", "games"]
    # season, games, final, to come, played, dnp, injury reports
    assert season[:7] == ["2026-27", "6", "3", "2", "9", "1", "7"]
    assert "Latest final game:         2026-10-23" in lines
    assert "First game not yet final:  2026-10-26 00:00 UTC" in lines
    assert "Latest injury report:      2026-11-19 22:30 UTC" in lines


def test_bad_season_is_a_usage_error(data_dir, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["nba", "sync", "--from", "last-year"])
    assert exit_info.value.code == 2
    assert "not a season" in capsys.readouterr().err
