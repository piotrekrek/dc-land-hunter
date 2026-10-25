"""The compose file splits the queue between two workers, and no job kind can fall between them."""

from __future__ import annotations

from pathlib import Path

import yaml

from dclh_pipeline.jobs import HANDLERS

COMPOSE = Path(__file__).parents[2] / "infra" / "docker-compose.yml"


def option(command: list[str], name: str) -> list[str]:
    return command[command.index(name) + 1].split(",") if name in command else []


def services() -> dict[str, list[str]]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {
        n: s["command"]
        for n, s in compose["services"].items()
        if s.get("command", [""])[:2] == ["dclh", "worker"]
    }


def test_there_is_a_general_worker_and_a_check_worker() -> None:
    assert set(services()) == {"worker", "check-worker"}


def test_the_check_worker_serves_only_checks_and_the_general_worker_never_takes_them() -> None:
    commands = services()
    assert option(commands["check-worker"], "--kinds") == ["check_parcel"]
    assert option(commands["worker"], "--exclude-kinds") == ["check_parcel"]
    assert option(commands["worker"], "--kinds") == []  # a new kind goes to the general worker by default


def test_every_handled_kind_has_exactly_one_worker() -> None:
    commands = services()
    for kind in HANDLERS:
        takers = [
            name
            for name, cmd in commands.items()
            if (not option(cmd, "--kinds") or kind in option(cmd, "--kinds"))
            and kind not in option(cmd, "--exclude-kinds")
        ]
        assert len(takers) == 1, f"{kind} is taken by {takers}"


def test_both_workers_exit_after_a_quiet_spell_so_the_container_restarts_lean() -> None:
    commands = services()
    assert option(commands["worker"], "--exit-after-idle") == ["900"]
    assert option(commands["check-worker"], "--exit-after-idle") == ["600"]
