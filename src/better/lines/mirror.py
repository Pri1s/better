"""PrizePicks line history from a public mirror on GitHub.

https://github.com/enkday/prizepicks-data-mirror commits one scrape of the
PrizePicks board per day and overwrites the previous day's file, so the history
lives in the repository's commits. This module keeps a bare clone under the raw
directory and turns every past version of the NBA file into a snapshot.

Things to know about the source (checked 2026-11-13):

* It has no licence. Use it for personal analysis and never redistribute it.
* It is collected upstream by an unofficial scraper of PrizePicks' API. This module
  only reads files the mirror has already published; it never calls PrizePicks.
* One scrape a day, near 12:05 UTC. Standard single-stat lines only after the first
  few days: no combos, no fantasy score, no goblins or demons.
* The row format changed over time, so the parser accepts every shape seen.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from better.lines import raw, rows

SOURCE = "mirror"
PARSER_VERSION = 1
REPO_URL = "https://github.com/enkday/prizepicks-data-mirror.git"
FEED_PATH = "data/prizepicks-nba.json"
CLONE_DIR = Path(raw.SUBDIR) / SOURCE / "repo.git"
GIT_TIMEOUT = 300  # seconds

_SHA = re.compile(r"^[0-9a-f]{40}$")
_CENTRAL = ZoneInfo("America/Chicago")
# Early files print the start as US Central wall-clock time and label it CST all year.
_CENTRAL_FORMAT = "%m/%d/%y %I:%M %p"


class MirrorError(RuntimeError):
    """The mirror could not be cloned, fetched or read."""


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at_utc: datetime


def sync_repo(raw_root: Path, url: str | None = None) -> Path:
    """Clone the mirror on first use, fetch new commits afterwards. Returns the clone."""
    url = url or REPO_URL
    repo = raw_root / CLONE_DIR
    if repo.exists():
        _git(repo, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*")
    else:
        repo.parent.mkdir(parents=True, exist_ok=True)
        _git(None, "clone", "--bare", "--quiet", url, str(repo))
    return repo


def feed_commits(repo: Path) -> list[Commit]:
    """Commits that changed the NBA file, oldest first."""
    output = _git(repo, "log", "--reverse", "--format=%H %cI", "HEAD", "--", FEED_PATH)
    commits = []
    for line in output.decode("utf-8").splitlines():
        sha, _, when = line.strip().partition(" ")
        moment = raw.parse_time(when)
        if _SHA.match(sha) and moment is not None:
            commits.append(Commit(sha, moment))
    return commits


def feed_at(repo: Path, commit: Commit) -> bytes:
    """The NBA file exactly as it was in one commit."""
    if not _SHA.match(commit.sha):
        raise MirrorError(f"not a commit id: {commit.sha!r}")
    return _git(repo, "show", f"{commit.sha}:{FEED_PATH}")


def capture(blob: bytes, commit: Commit, url: str | None = None) -> raw.Envelope:
    """Wrap one version of the file as a snapshot.

    The snapshot is dated by the mirror's own scrape time, because that is when the
    lines were on the board. The commit time is the fallback for a file that does not
    say.
    """
    body = blob.decode("utf-8", errors="replace")
    fetched_at = _scrape_time(body) or commit.committed_at_utc
    page = (url or REPO_URL).removesuffix(".git") + f"/blob/{commit.sha}/{FEED_PATH}"
    return raw.Envelope(
        source=SOURCE,
        fetched_at_utc=fetched_at.replace(microsecond=0),
        meta={"commit": commit.sha, "path": FEED_PATH},
        responses=(raw.Response(page, 200, commit.committed_at_utc.replace(microsecond=0), body),),
    )


def import_new(
    raw_root: Path,
    known_commits: Iterable[str] = (),
    *,
    url: str | None = None,
    log: Callable[[str], None] = print,
) -> list[Path]:
    """Save a snapshot file for every commit not seen before. Returns the new files.

    ``known_commits`` are the commits already in the database. A commit whose
    snapshot file already exists is skipped as well, so an interrupted import can be
    run again safely.
    """
    repo = sync_repo(raw_root, url)
    known = set(known_commits)
    written: list[Path] = []
    for commit in feed_commits(repo):
        if commit.sha in known:
            continue
        envelope = capture(feed_at(repo, commit), commit, url)
        path = raw_root / envelope.relative_path
        if path.exists():
            # Either this commit was saved by an earlier run, or another commit carries
            # the same scrape time. The second case is worth a note; the first is not.
            existing = raw.read(path)
            if (existing.meta.get("commit") != commit.sha
                    and existing.responses[0].body != envelope.responses[0].body):
                log(f"mirror: commit {commit.sha[:8]} has the scrape time of commit "
                    f"{str(existing.meta.get('commit'))[:8]} but different lines; kept the first")
            continue
        written.append(raw.write(envelope, raw_root))
    return written


def parse(envelope: raw.Envelope) -> rows.Parsed:
    """Turn one day's file into line rows, whatever shape its rows have."""
    parsed = rows.Parsed(detail=dict(envelope.meta))
    document = json.loads(envelope.responses[0].body)
    props = document.get("props") if isinstance(document, dict) else None
    if not isinstance(props, list):
        raise ValueError("no props list in the mirror file")

    status = document.get("collectionStatus")
    if isinstance(status, dict) and status.get("sourceCount") is not None:
        # How many projections PrizePicks returned before the mirror filtered them.
        parsed.detail["upstream_projections"] = status.get("sourceCount")

    for prop in props:
        parsed.seen += 1
        if not isinstance(prop, dict):
            parsed.drop("not_an_object")
            continue
        sport = rows.text(prop.get("sport"))
        if sport is not None and sport.upper() != "NBA":
            parsed.drop("other_league")
            continue
        player = rows.text(prop.get("player"))
        stat = rows.text(prop.get("stat"))
        line = rows.to_float(prop.get("line"))
        # The start time moved between fields over the mirror's history: an ISO time in
        # startTimeIso, an ISO time in startTime, or Central wall-clock text in either
        # startTimeCST or startTime.
        start = (
            raw.parse_time(prop.get("startTimeIso"))
            or raw.parse_time(prop.get("startTime"))
            or _central_time(prop.get("startTimeCST"))
            or _central_time(prop.get("startTime"))
        )
        if player is None:
            parsed.drop("no_player")
        elif stat is None:
            parsed.drop("no_stat")
        elif line is None:
            parsed.drop("bad_line")
        elif start is None:
            # Without a start time the game cannot be identified.
            parsed.drop("no_start_time")
        else:
            team = rows.text(prop.get("Team"))
            parsed.rows.append(
                rows.LineRow(
                    book=rows.PRIZEPICKS,
                    league="NBA",
                    player_name=player,
                    stat_name=stat,
                    line=line,
                    odds_type=(rows.text(prop.get("oddsType")) or rows.STANDARD).lower(),
                    source_line_id=rows.text(prop.get("projectionId")),
                    source_player_id=rows.text(prop.get("playerId")),
                    source_game_id=rows.text(prop.get("gameId")),
                    team_code=rows.text(prop.get("teamCode")),
                    team_name=team,
                    opponent_code=rows.text(prop.get("opponentCode")),
                    opponent_name=rows.text(prop.get("Opponent")),
                    start_time_utc=start,
                    status=rows.text(prop.get("status")),
                    is_combo=_is_combo(player, team),
                    board_rank=rows.to_int(prop.get("rank")),
                )
            )
    return parsed


def _is_combo(player: str, team: str | None) -> bool:
    """Two players on one line: 'A + B', with the team printed as 'BKN/NOP'."""
    return " + " in player or (team is not None and "/" in team)


def _scrape_time(body: str) -> datetime | None:
    try:
        document = json.loads(body)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    status = document.get("collectionStatus")
    provider = status.get("providerFetchedAt") if isinstance(status, dict) else None
    return raw.parse_time(provider) or raw.parse_time(document.get("scrapedAt"))


def _central_time(value: object) -> datetime | None:
    """'12/06/25 04:10 PM CST' as UTC. The label says CST even during daylight time."""
    cleaned = rows.text(value)
    if cleaned is None:
        return None
    cleaned = re.sub(r"\s*(CST|CDT|CT)$", "", cleaned)
    try:
        local = datetime.strptime(cleaned, _CENTRAL_FORMAT).replace(tzinfo=_CENTRAL)
    except ValueError:
        return None
    return local.astimezone(UTC).replace(tzinfo=None)


def _git(repo: Path | None, *args: str) -> bytes:
    command = ["git"]
    if repo is not None:
        command += ["--git-dir", str(repo)]
    command += list(args)
    # Never stop to ask for credentials: the mirror is public, and a prompt would hang.
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            command, capture_output=True, check=False, timeout=GIT_TIMEOUT, env=environment
        )
    except FileNotFoundError as exc:
        raise MirrorError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise MirrorError(f"git {args[0]} timed out after {GIT_TIMEOUT} seconds") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise MirrorError(f"git {args[0]} failed: {message[-1] if message else 'no output'}")
    return result.stdout
