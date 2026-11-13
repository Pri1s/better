"""Line fixtures shaped like what the sources really send.

The mirror documents below reproduce every row shape found in the mirror's history
(surveyed 2026-11-13): the current shape, the mid-2026 shape without provider ids,
and the two early shapes without an ISO start time. Values are invented; the
players, teams and games are the ones in ``sourcefiles`` (season 2027), so that the
rows can be linked and graded in later tests.

    game 103  Oct 23, 2026  BOS @ ATL  tip-off 23:30 UTC (7:30 pm Eastern)  final
    game 104  Oct 25, 2026  ATL @ NO   tip-off 00:00 UTC on the 26th         scheduled
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

GAME_103_START = "2026-10-23T19:30:00.000-04:00"
GAME_104_START = "2026-10-25T20:00:00.000-04:00"


def current_row(**changes) -> dict:
    """A row as the mirror writes it today: provider ids, status, the line as text."""
    row = {
        "projectionId": "16915616",
        "playerId": "188005",
        "status": "pre_game",
        "player": "Jayson Tatum",
        "stat": "Points",
        "line": "27.5",
        "sport": "NBA",
        "startTime": "10/23/26 06:30 PM CST",
        "startTimeCST": "10/23/26 06:30 PM CST",
        "startDateCST": "2026-10-23",
        "startTimeIso": GAME_103_START,
        "oddsType": "standard",
        "Team": "Boston Celtics",
        "teamCode": "BOS",
        "Opponent": "Atlanta Hawks",
        "opponentCode": "ATL",
        "rank": 101,
        "gameId": "195018",
        "providerFetchedAt": "2026-10-23T12:06:28.573Z",
    }
    row.update(changes)
    return row


def middle_row(**changes) -> dict:
    """Mid-2026: no provider ids or status, and the line as a number."""
    row = {
        "player": "Trae Young",
        "stat": "Assists",
        "line": 10.5,
        "sport": "NBA",
        "startTime": "10/23/26 06:30 PM CST",
        "startTimeCST": "10/23/26 06:30 PM CST",
        "startDateCST": "2026-10-23",
        "startTimeIso": GAME_103_START,
        "oddsType": "standard",
        "Team": "Atlanta Hawks",
        "teamCode": "ATL",
        "Opponent": "Boston Celtics",
        "opponentCode": "BOS",
        "rank": 71,
        "gameId": "126072",
    }
    row.update(changes)
    return row


def early_row(**changes) -> dict:
    """December 2025: the team as a full name, no codes, the start in Central time only."""
    row = {
        "player": "Derrick White",
        "stat": "3-PT Made",
        "line": 2.5,
        "sport": "NBA",
        "startTime": "10/23/26 06:30 PM CST",
        "oddsType": "standard",
        "Team": "Boston Celtics",
        "Opponent": "Atlanta Hawks",
        "gameId": "111628",
    }
    row.update(changes)
    return row


def teamless_row(**changes) -> dict:
    """The second day of the mirror: an ISO start time in ``startTime`` and no team at all."""
    row = {
        "player": "Trae Young",
        "stat": "Points",
        "line": 31.5,
        "sport": "NBA",
        "startTime": GAME_103_START,
        "oddsType": "demon",
    }
    row.update(changes)
    return row


def bare_row(**changes) -> dict:
    """The very first commits: a player, a stat and a line, and nothing else."""
    row = {"player": "Jayson Tatum", "stat": "Rebounds", "line": 8.5, "sport": "NBA"}
    row.update(changes)
    return row


def document(props: list[dict], scraped_at: str = "2026-10-23T12:07:33.431Z",
             provider_at: str | None = "2026-10-23T12:06:28.573Z") -> str:
    """The text of one version of ``data/prizepicks-nba.json``."""
    body = {
        "scrapedAt": scraped_at,
        "scrapedDate": "Friday, October 23, 2026 at 07:07 AM CDT",
        "source": "PrizePicks API",
        "sport": "NBA",
        "totalProps": len(props),
        "props": props,
    }
    if provider_at is not None:
        body["collectionStatus"] = {
            "leagueName": "NBA", "ok": True, "status": 200, "count": len(props),
            "sourceCount": 3051, "providerFetchedAt": provider_at,
            "endpoint": "https://partner-api.prizepicks.com/projections",
        }
    return json.dumps(body, indent=2)


# One day's file holding every shape, plus rows a parser has to refuse.
MIXED_PROPS = [
    current_row(),
    middle_row(),
    early_row(),
    bare_row(),                                                    # no start time: dropped
    current_row(player="Jayson Tatum + Trae Young", Team="BOS/ATL", teamCode=None,
                stat="Points", line="52.5", projectionId="16915700"),  # combo
    current_row(stat="Rebounds", line="6.5", oddsType="goblin", projectionId="16915701"),
    current_row(line="not a number", projectionId="16915702"),      # dropped
    current_row(player="  ", projectionId="16915703"),              # dropped
    current_row(sport="NFL", projectionId="16915704"),              # dropped
    current_row(player="Dejounte Murray", stat="Points", line=18, Team="New Orleans Pelicans",
                teamCode="NOP", Opponent="Atlanta Hawks", opponentCode="ATL",
                startTimeIso=GAME_104_START, projectionId="16915705"),
    teamless_row(),
]


def git(repo: Path, *args: str, when: str | None = None) -> str:
    """Run git in a fixture repository with a fixed identity (and commit time, if given)."""
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
        # Keep the developer's own git settings (signing, hooks, templates) out of it.
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    }
    if when is not None:
        environment["GIT_AUTHOR_DATE"] = environment["GIT_COMMITTER_DATE"] = when
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, env=environment
    )
    return result.stdout.decode().strip()


def mirror_repo(directory: Path) -> Path:
    """A small stand-in for the mirror: three days of the NBA file and an unrelated commit."""
    repo = directory / "mirror-upstream"
    (repo / "data").mkdir(parents=True)
    git(repo, "init", "--quiet", "--initial-branch", "main")
    commit_feed(repo, document([current_row()], "2026-10-21T12:07:00.000Z",
                               "2026-10-21T12:06:00.000Z"), "2026-10-21T12:07:10+00:00")
    (repo / "README.md").write_text("mirror\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "docs", when="2026-10-21T13:00:00+00:00")
    commit_feed(repo, document([current_row(line="28.5"), middle_row()],
                               "2026-10-22T12:07:00.000Z", "2026-10-22T12:06:00.000Z"),
                "2026-10-22T12:07:10+00:00")
    commit_feed(repo, document(MIXED_PROPS), "2026-10-23T12:07:40+00:00")
    return repo


def commit_feed(repo: Path, text: str, when: str) -> str:
    """Overwrite the NBA file, commit it and return the commit id."""
    (repo / "data" / "prizepicks-nba.json").write_text(text)
    git(repo, "add", "data/prizepicks-nba.json")
    git(repo, "commit", "--quiet", "-m", "Update PrizePicks data", when=when)
    return git(repo, "rev-parse", "HEAD")
