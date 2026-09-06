"""The genesis service graph -- read the compose file, and refuse what it must never contain.

PURPOSE
    ``deploy/docker-compose.genesis.yml`` is the floor a node needs running
    before its stores can exist. This module READS that file and answers four
    questions no reviewer should have to answer by eye on every change:
    does every service declare a healthcheck; is every published host port
    registered in ``config/ports.yaml`` and bound to loopback; is every image
    tag pinned rather than floating; and is any image on the refused list --
    an estate service, or a store under a source-available licence that a
    public clone must not inherit.

    It is a DETECTOR. It reports findings; it never edits the compose file and
    never starts anything. Nothing in this module opens a socket.

WRITE MODEL
    None. Pure reads of two files, both of which are human-authored
    registries with their own single-writer models stated in their headers.

BLIND SPOTS
    - It reads the compose file as YAML. It does not run ``docker compose
      config``, so it cannot catch an error that only the compose
      implementation's own schema knows about, and it resolves variable
      expansion itself for the two forms this file uses (``${VAR:-default}``
      and ``${VAR:?message}``) rather than for the whole expansion grammar. A
      third form would be reported as unresolved, not silently accepted.
    - A healthcheck being DECLARED is not a healthcheck passing. This module
      checks presence and refuses the disabling form; whether the probe is a
      good one is a judgement it does not make.
    - The refused-image list is by NAME. An estate service re-tagged under a
      different name is invisible to it, which is why the list is a floor and
      the reviewer is still the ceiling.
    - It says nothing about a licence it cannot read. The licence lines live
      in the compose file's own header, written by a human who checked; this
      module only enforces that a refused image is absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

__all__ = [
    "REFUSED_IMAGES",
    "Finding",
    "GraphError",
    "compose_path",
    "load_compose",
    "load_registered_ports",
    "expand",
    "published_ports",
    "check_graph",
    "render_plan",
    "selftest",
]

#: Images a genesis graph refuses, each with the reason it is refused.
#:
#: Two classes, and they are different refusals. A source-available licence is
#: refused because a public clone must not inherit an obligation its operator
#: never chose. A capability app is refused because it is somebody's
#: deployment: an application or another project's API is never the
#: framework's floor, however useful it is on the machine it runs on.
#:
#: Every name here is a PUBLICLY NAMED project. A deployment-specific service
#: is deliberately absent from this list rather than enumerated: a list of one
#: operator's private service names, shipped publicly, would tell strangers
#: what that operator runs. Those are the reviewer's call, and the blind-spot
#: note above says so.
REFUSED_IMAGES: Tuple[Tuple[str, str], ...] = (
    ("falkordb", "graph store under a source-available licence; a genesis node "
                 "has no graph organ at all -- see docs/SUBSTRATE.md"),
    ("redis", "the cache fork whose terms changed; Valkey (BSD 3-Clause) is "
              "the public default and is wire-compatible"),
    ("firefly", "a finance application: a capability app, not framework "
                "substrate"),
    ("zitadel", "an identity provider the node has never used; built-in JWT is "
                "the default and an IdP is an opt-in upgrade"),
)

_EXPANSION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-|:\?)?([^}]*)\}")


class GraphError(RuntimeError):
    """The compose file could not be read at all."""


@dataclass(frozen=True)
class Finding:
    """One refusal, with the service it is about and why it fired."""

    check: str
    service: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.check}] {self.service}: {self.detail}"


def compose_path(repo_root: Path) -> Path:
    return Path(repo_root) / "deploy" / "docker-compose.genesis.yml"


def load_compose(path: Path) -> Mapping[str, Any]:
    """Read the compose file, or HALT saying which file and why."""
    if not path.exists():
        raise GraphError(f"{path} is missing -- the genesis graph is not optional")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GraphError(f"{path} does not parse as YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise GraphError(f"{path} is not a mapping at the top level")
    if not isinstance(data.get("services"), Mapping):
        raise GraphError(f"{path} declares no `services` mapping")
    return data


def load_registered_ports(repo_root: Path) -> Dict[int, Dict[str, Any]]:
    """The port registry, keyed by port number.

    A missing registry is a HALT rather than an empty dict: an empty registry
    would make every published port unregistered and the check would read as
    catastrophic, or -- worse, if it were written the other way -- as clean.
    """
    path = Path(repo_root) / "config" / "ports.yaml"
    if not path.exists():
        raise GraphError(f"{path} is missing -- nothing can say which ports are registered")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = (data or {}).get("services")
    if not isinstance(services, Mapping):
        raise GraphError(f"{path} declares no `services` mapping")
    out: Dict[int, Dict[str, Any]] = {}
    for name, row in services.items():
        if not isinstance(row, Mapping) or "port" not in row:
            raise GraphError(f"{path}: service {name!r} declares no port")
        out[int(row["port"])] = {"name": name, **dict(row)}
    return out


def expand(value: str) -> Tuple[str, bool]:
    """Resolve the two expansion forms this file uses.

    Returns ``(text, resolved)``. ``${VAR:-default}`` resolves to its default.
    ``${VAR:?message}`` deliberately does NOT resolve: it is the form that
    makes compose refuse to start without an operator-supplied value, and
    pretending to know that value here would be inventing it.
    """
    resolved = True

    def one(match: "re.Match[str]") -> str:
        nonlocal resolved
        operator, tail = match.group(2), match.group(3)
        if operator == ":-":
            return tail
        resolved = False
        return match.group(0)

    return _EXPANSION.sub(one, value), resolved


@dataclass(frozen=True)
class PublishedPort:
    service: str
    host_ip: str
    host_port: Optional[int]
    container_port: str
    raw: str


def published_ports(compose: Mapping[str, Any]) -> List[PublishedPort]:
    """Every host port this graph publishes, with its binding."""
    out: List[PublishedPort] = []
    for name, svc in compose["services"].items():
        for entry in (svc or {}).get("ports", []) or []:
            if isinstance(entry, Mapping):
                # long syntax
                host_raw = str(entry.get("published", ""))
                text, ok = expand(host_raw)
                out.append(PublishedPort(
                    name, str(entry.get("host_ip", "")),
                    int(text) if ok and text.isdigit() else None,
                    str(entry.get("target", "")), host_raw))
                continue
            text, ok = expand(str(entry))
            parts = text.split(":")
            if len(parts) == 3:
                host_ip, host_port, container = parts
            elif len(parts) == 2:
                host_ip, host_port, container = "", parts[0], parts[1]
            else:
                host_ip, host_port, container = "", "", parts[0]
            out.append(PublishedPort(
                name, host_ip,
                int(host_port) if host_port.isdigit() else None,
                container, str(entry)))
    return out


def check_graph(repo_root: Path) -> Tuple[bool, List[Finding], Dict[str, Any]]:
    """Read the graph and report every refusal. Never raises on a finding."""
    compose = load_compose(compose_path(repo_root))
    registry = load_registered_ports(repo_root)
    findings: List[Finding] = []
    services: Mapping[str, Any] = compose["services"]

    for name, svc in services.items():
        svc = svc or {}

        image = str(svc.get("image", ""))
        if not image:
            findings.append(Finding("image-declared", name, "declares no image"))
        else:
            text, _ = expand(image)
            lowered = text.lower()
            for refused, reason in REFUSED_IMAGES:
                if refused in lowered:
                    findings.append(Finding(
                        "refused-image", name,
                        f"image {text!r} matches refused name {refused!r}: {reason}"))
            tag = text.rsplit(":", 1)[-1] if ":" in text.rsplit("/", 1)[-1] else ""
            if not tag:
                findings.append(Finding(
                    "image-tag-pinned", name,
                    f"image {text!r} carries no tag; an implicit tag is a build "
                    "that cannot be reproduced"))
            elif tag == "latest":
                findings.append(Finding(
                    "image-tag-pinned", name,
                    "image tag is `latest`; a floor that changes under you is "
                    "not a floor"))

        health = svc.get("healthcheck")
        if not isinstance(health, Mapping):
            findings.append(Finding(
                "healthcheck-declared", name,
                "declares no healthcheck; an unprobed service leaves the "
                "population, and a graph with a missing member reads healthier "
                "than one with a failing member"))
        else:
            if health.get("disable") is True:
                findings.append(Finding(
                    "healthcheck-declared", name,
                    "healthcheck is explicitly disabled"))
            if not health.get("test"):
                findings.append(Finding(
                    "healthcheck-declared", name,
                    "healthcheck declares no test"))

    for port in published_ports(compose):
        if port.host_port is None:
            findings.append(Finding(
                "port-registered", port.service,
                f"published port {port.raw!r} does not resolve to a number here"))
            continue
        if port.host_ip != "127.0.0.1":
            findings.append(Finding(
                "port-loopback", port.service,
                f"port {port.host_port} binds to {port.host_ip or '0.0.0.0'!r}, "
                "not loopback; a routable default is an operator's explicit act"))
        if port.host_port not in registry:
            findings.append(Finding(
                "port-registered", port.service,
                f"host port {port.host_port} is not in config/ports.yaml -- "
                "register it there rather than hardcoding a literal"))

    for volume in (compose.get("volumes") or {}):
        if not volume:
            findings.append(Finding("volume-named", "volumes", "an unnamed volume"))

    summary = {
        "services": sorted(services),
        "published": [
            {"service": p.service, "host_port": p.host_port,
             "container_port": p.container_port, "host_ip": p.host_ip}
            for p in published_ports(compose)],
        "profiles": sorted({
            prof for svc in services.values()
            for prof in ((svc or {}).get("profiles") or [])}),
        "volumes": sorted((compose.get("volumes") or {})),
        "findings": [str(f) for f in findings],
    }
    return (not findings, findings, summary)


def render_plan(repo_root: Path) -> str:
    """A human-readable plan of what would run. Connects to nothing."""
    ok, findings, summary = check_graph(repo_root)
    registry = load_registered_ports(repo_root)
    compose = load_compose(compose_path(repo_root))
    lines: List[str] = []
    lines.append("GENESIS SERVICE PLAN (rendered; nothing was started or contacted)")
    lines.append(f"  compose file: {compose_path(repo_root)}")
    lines.append("")
    for name in summary["services"]:
        svc = compose["services"][name] or {}
        image, _ = expand(str(svc.get("image", "")))
        profiles = svc.get("profiles") or []
        posture = ("OPTIONAL, profile " + ",".join(profiles)) if profiles else "REQUIRED"
        lines.append(f"  {name:<10} {image}")
        lines.append(f"             {posture}")
        for port in published_ports(compose):
            if port.service != name or port.host_port is None:
                continue
            reg = registry.get(port.host_port, {})
            lines.append(
                f"             {port.host_ip}:{port.host_port} -> "
                f"{port.container_port}  (ports.yaml: {reg.get('name', 'UNREGISTERED')})")
    lines.append("")
    lines.append(f"  profiles: {', '.join(summary['profiles']) or 'none'}")
    lines.append(f"  volumes:  {', '.join(summary['volumes']) or 'none'}")
    lines.append("")
    if ok:
        lines.append("  graph: CLEAN")
    else:
        lines.append(f"  graph: {len(findings)} FINDING(S)")
        for finding in findings:
            lines.append(f"    - {finding}")
    return "\n".join(lines)


def selftest(repo_root: Optional[Path] = None) -> Tuple[bool, str]:
    """Prove every check above can actually fire, on synthetic graphs.

    A detector that has never fired is indistinguishable from a broken one, so
    each check is fired against a deliberately broken graph written to a temp
    directory -- never against the shipped tree.
    """
    import tempfile

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    fired: List[str] = []
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        fired.append(name)
        if not ok:
            failures.append(name)

    def fires(check: str, services: Mapping[str, Any]) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / "deploy").mkdir()
            (fake / "config").mkdir()
            compose_path(fake).write_text(
                yaml.safe_dump({"services": dict(services)}), encoding="utf-8")
            (fake / "config" / "ports.yaml").write_text(
                yaml.safe_dump({"services": {"vector_store": {"port": 8002}}}),
                encoding="utf-8")
            _, found, _ = check_graph(fake)
            return any(f.check == check for f in found)

    healthy = {"image": "example/thing:1.0",
               "healthcheck": {"test": ["CMD", "true"]},
               "ports": ["127.0.0.1:8002:5432"]}

    expect("clean-graph-is-clean", not fires("refused-image", {"a": healthy})
           and not fires("healthcheck-declared", {"a": healthy})
           and not fires("port-registered", {"a": healthy}))
    expect("refused-image-fires", fires("refused-image", {
        "a": {**healthy, "image": "falkordb/falkordb:1.0"}}))
    expect("missing-healthcheck-fires", fires("healthcheck-declared", {
        "a": {"image": "example/thing:1.0"}}))
    expect("disabled-healthcheck-fires", fires("healthcheck-declared", {
        "a": {**healthy, "healthcheck": {"disable": True, "test": ["CMD", "true"]}}}))
    expect("unregistered-port-fires", fires("port-registered", {
        "a": {**healthy, "ports": ["127.0.0.1:9999:5432"]}}))
    expect("non-loopback-fires", fires("port-loopback", {
        "a": {**healthy, "ports": ["0.0.0.0:8002:5432"]}}))
    expect("latest-tag-fires", fires("image-tag-pinned", {
        "a": {**healthy, "image": "example/thing:latest"}}))
    expect("untagged-image-fires", fires("image-tag-pinned", {
        "a": {**healthy, "image": "example/thing"}}))
    expect("no-image-fires", fires("image-declared", {
        "a": {"healthcheck": {"test": ["CMD", "true"]}}}))

    text, ok = expand("${FOO:-8002}")
    expect("expansion-default-resolves", ok and text == "8002")
    _, ok = expand("${FOO:?required}")
    expect("required-expansion-stays-unresolved", not ok)

    shipped_ok, shipped_findings, _ = check_graph(root)
    expect("shipped-graph-clean", shipped_ok)
    if not shipped_ok:
        failures.append("shipped: " + "; ".join(str(f) for f in shipped_findings))

    report = (f"service graph selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read and check the genesis service graph. Starts nothing.")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.repo_root) if args.repo_root else Path(
        __file__).resolve().parents[3]
    if args.selftest:
        ok, report = selftest(root)
        print(report)
        return 0 if ok else 1
    print(render_plan(root))
    ok, findings, _ = check_graph(root)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
