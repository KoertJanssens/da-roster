#!/usr/bin/env python3
"""Generate 3 non-overlapping rosters from Raid-Helper event endpoints.

Usage:
  python generate_rosters.py 149... 149... 149...
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

API_URL = "https://raid-helper.xyz/api/v4/events/{id}"


@dataclass(frozen=True)
class Player:
    name: str
    role: str


ROLE_HINTS = {
    "tank": {"tank", "tanks"},
    "healer": {"healer", "healers", "heal"},
    "dps": {"dps", "damage", "mdps", "rdps", "melee", "ranged"},
}


def fetch_raidplan(event_id: str) -> dict[str, Any]:
    req = Request(API_URL.format(id=event_id), headers={"User-Agent": "da-roster-bot/1.0"})
    with urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{event_id}: expected HTTP 200, got {response.status}")
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def normalize_role(raw: Any) -> str:
    txt = str(raw or "").strip().lower()
    for normalized, hints in ROLE_HINTS.items():
        if txt in hints:
            return normalized
    return "dps"


def _extract_name(candidate: dict[str, Any]) -> str | None:
    for key in ("name", "username", "displayName", "character", "nick", "nickname"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    user = candidate.get("user")
    if isinstance(user, dict):
        for key in ("name", "username", "global_name", "display_name"):
            value = user.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_role(candidate: dict[str, Any]) -> str:
    for key in ("roleName", "role", "spec", "classRole", "raidRole", "selectedRole"):
        if key in candidate:
            return normalize_role(candidate.get(key))
    return "dps"


def collect_players(obj: Any) -> list[Player]:
    """Extract players from raid-helper `/api/v4/events/{id}` payloads."""
    found: list[Player] = []

    if not isinstance(obj, dict):
        return found

    signups = obj.get("signUps")
    if not isinstance(signups, list):
        return found

    for signup in signups:
        if not isinstance(signup, dict):
            continue
        # Keep only active signups and skip absences.
        if str(signup.get("status", "")).lower() != "primary":
            continue
        if str(signup.get("className", "")).strip().lower() == "absence":
            continue
        if "userId" not in signup:
            continue

        name = _extract_name(signup)
        if not name:
            continue
        found.append(Player(name=name, role=_extract_role(signup)))

    dedup: dict[str, Player] = {}
    for p in found:
        key = p.name.casefold()
        if key not in dedup:
            dedup[key] = p
    return sorted(dedup.values(), key=lambda p: p.name.casefold())


def assign_unique_rosters(players_per_event: dict[str, list[Player]]) -> dict[str, list[Player]]:
    taken: set[str] = set()
    rosters: dict[str, list[Player]] = {}
    for event_id, players in players_per_event.items():
        roster: list[Player] = []
        for player in players:
            key = player.name.casefold()
            if key in taken:
                continue
            taken.add(key)
            roster.append(player)
        rosters[event_id] = roster
    return rosters


def write_markdown(path: Path, rosters: dict[str, list[Player]]) -> None:
    lines = ["# Event Rosters", "", "_No player appears in more than one roster._", ""]

    for event_id, roster in rosters.items():
        lines.append(f"## Event `{event_id}`")
        lines.append("")
        if not roster:
            lines.append("No assignable players found.")
            lines.append("")
            continue
        lines.append("| Player | Role |")
        lines.append("|---|---|")
        for p in roster:
            lines.append(f"| {p.name} | {p.role} |")
        lines.append("")

    role_order = ["tank", "healer", "dps"]
    lines.extend(["## Visual Overview", "", "```text"])
    for event_id, roster in rosters.items():
        counts = {r: 0 for r in role_order}
        for p in roster:
            counts[p.role] = counts.get(p.role, 0) + 1
        bar = " ".join(f"{role[:1].upper()}:{'#' * counts[role]} ({counts[role]})" for role in role_order)
        lines.append(f"{event_id}: {bar}")
    lines.append("```")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html(path: Path, rosters: dict[str, list[Player]]) -> None:
    sections: list[str] = []
    for event_id, roster in rosters.items():
        rows = "".join(
            f"<tr><td>{escape(p.name)}</td><td>{escape(p.role)}</td></tr>" for p in roster
        )
        if not rows:
            rows = '<tr><td colspan="2"><em>No assignable players found.</em></td></tr>'
        sections.append(
            f"<section><h2>Event {escape(event_id)}</h2>"
            "<table><thead><tr><th>Player</th><th>Role</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></section>"
        )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Roster Overview</title>"
        "<style>body{font-family:Arial,sans-serif;margin:2rem;}"
        "section{margin-bottom:1.5rem;}table{border-collapse:collapse;width:100%;max-width:520px;}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;}th{background:#f5f5f5;}</style>"
        "</head><body><h1>Event Rosters</h1><p>No player appears in more than one roster.</p>"
        + "".join(sections)
        + "</body></html>"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_ids", nargs=3, help="Three raid-helper event ids")
    args = parser.parse_args()

    players_per_event: dict[str, list[Player]] = {}
    errors: list[str] = []
    for event_id in args.event_ids:
        try:
            data = fetch_raidplan(event_id)
            players_per_event[event_id] = collect_players(data)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append(f"{event_id}: {exc}")
            players_per_event[event_id] = []

    rosters = assign_unique_rosters(players_per_event)

    write_markdown(Path("output/rosters.md"), rosters)
    write_html(Path("output/rosters.html"), rosters)

    if errors:
        print("Completed with fetch/parsing issues:")
        for err in errors:
            print(f"- {err}")
        return 2

    print("Wrote output/rosters.md and output/rosters.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
