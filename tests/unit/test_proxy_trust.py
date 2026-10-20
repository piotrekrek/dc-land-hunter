"""The API may only trust X-Forwarded-For from Caddy (infra/). A wildcard lets any client pick its own IP."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[2] / "infra"


def _trusted_proxies(command: list[str]) -> list[str]:
    i = command.index("--forwarded-allow-ips")
    return command[i + 1].split(",")


def test_compose_api_trusts_only_the_web_container_address() -> None:
    compose = yaml.safe_load((INFRA / "docker-compose.yml").read_text(encoding="utf-8"))
    api_command = compose["services"]["api"]["command"]
    trusted = _trusted_proxies(api_command)

    assert "--proxy-headers" in api_command
    assert trusted == ["172.30.0.10"]
    assert "*" not in trusted


def test_web_container_has_the_fixed_address_inside_the_subnet() -> None:
    compose = yaml.safe_load((INFRA / "docker-compose.yml").read_text(encoding="utf-8"))
    web_ip = compose["services"]["web"]["networks"]["default"]["ipv4_address"]
    subnet = compose["networks"]["default"]["ipam"]["config"][0]["subnet"]

    assert ipaddress.ip_address(web_ip) in ipaddress.ip_network(subnet)
    assert web_ip == _trusted_proxies(compose["services"]["api"]["command"])[0]


def test_image_default_trusts_no_wildcard() -> None:
    dockerfile = (INFRA / "docker" / "python.Dockerfile").read_text(encoding="utf-8")
    cmd = re.search(r"^CMD (\[.*\])$", dockerfile, re.MULTILINE)
    assert cmd is not None
    assert "*" not in _trusted_proxies(yaml.safe_load(cmd.group(1)))


def test_caddy_replaces_forwarded_for_on_api_requests() -> None:
    caddyfile = (INFRA / "Caddyfile").read_text(encoding="utf-8")
    api_block = re.search(r"handle /api/\* \{(.*?)\n\t\}", caddyfile, re.DOTALL)
    assert api_block is not None
    assert "header_up X-Forwarded-For {remote_host}" in api_block.group(1)
