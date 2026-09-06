"""Come online -- how a node proves it is alive by ANSWERING, not asserting.

PURPOSE
    Six questions, each answered by a named instrument carrying its as-of.
    There is no ``status: healthy`` field anywhere in the certificate, and
    there is no ``OK`` in the verdict vocabulary, because zero output is a
    state and not a success.

    Every rate is printed WITH its denominator -- "probes 10/10", never
    "100%" -- since a rate without its population is not a measurement. A
    check that cannot run stays IN the denominator as ``UNPROBEABLE`` with its
    reason: an instrument that quietly leaves the count makes the number
    better-looking, which is exactly why nobody goes looking.

WRITE MODEL
    ``.intentops/genesis/aliveness.jsonl`` -- append-only under ``StoreLock``,
    state as a pure fold over the readings. Nothing is ever edited in place.
    ``.intentops/genesis/birth-certificate.md`` is a GENERATED projection of
    the latest reading: correct the instruments, never the certificate.

BLIND SPOTS
    - Check 2 (belief currency) is UNPROBEABLE at birth by construction: no
      carrier specification is bound yet, and running the instrument over an
      empty population would score a perfect reading by construction -- the
      most flattering possible answer to a question nobody asked. It is
      reported as unprobeable rather than skipped.
    - Check 5 exercises the gate's own classifier in-process. It proves the
      classifier can refuse; it does NOT prove the host honours the refusal,
      which is the saddle's S2 operation and is verified by the saddle
      contract tests, not here.
    - A certificate says what six instruments answered at one moment. It is a
      reading, not a warranty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..store_guard import StoreLock, atomic_replace, lock_for

__all__ = [
    "CheckAnswer",
    "Reading",
    "VERDICTS",
    "ANSWER_STATUSES",
    "take_reading",
    "render_certificate",
    "append_reading",
    "selftest",
]

#: Closed. There is no ``OK``.
VERDICTS: Tuple[str, ...] = ("ALIVE", "ALIVE-DEGRADED", "NOT-ALIVE", "STOOD-DOWN")

#: Closed. ``UNPROBEABLE`` is a value the consumer counts, never an absence.
ANSWER_STATUSES: Tuple[str, ...] = ("ANSWERED", "UNPROBEABLE", "FAILED")

ALIVENESS_RELPATH = Path(".intentops") / "genesis" / "aliveness.jsonl"
CERTIFICATE_RELPATH = Path(".intentops") / "genesis" / "birth-certificate.md"


def decision_name(verdict: Any) -> str:
    """The verdict's decision as an upper-case string, whatever its type.

    Read through one function rather than compared inline: an enum whose
    ``value`` case differs from the comparison is a silent wrong answer, and
    that is exactly how a gate check comes to report that a plain read was
    refused.
    """
    decision = getattr(verdict, "decision", verdict)
    return str(getattr(decision, "value", decision)).upper()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class CheckAnswer:
    """One birth question and the instrument's answer to it."""

    number: int
    question: str
    instrument: str
    status: str
    detail: str
    counts: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {"number": self.number, "question": self.question,
                "instrument": self.instrument, "status": self.status,
                "detail": self.detail, "counts": self.counts}


@dataclass
class Reading:
    as_of: str
    answers: List[CheckAnswer] = field(default_factory=list)
    node_root: str = ""
    designation: str = ""
    dry_run: bool = False
    stood_down: bool = False

    @property
    def verdict(self) -> str:
        if self.stood_down:
            return "STOOD-DOWN"
        if any(a.status == "FAILED" for a in self.answers):
            return "NOT-ALIVE"
        if any(a.status == "UNPROBEABLE" for a in self.answers):
            return "ALIVE-DEGRADED"
        return "ALIVE"

    @property
    def faculties_absent(self) -> List[str]:
        """Named, never implied: which faculty, and why."""
        return [f"check {a.number} ({a.instrument}): {a.detail}"
                for a in self.answers if a.status != "ANSWERED"]

    def to_row(self) -> Dict[str, Any]:
        return {"as_of": self.as_of, "verdict": self.verdict,
                "node_root": self.node_root, "designation": self.designation,
                "dry_run": self.dry_run, "stood_down": self.stood_down,
                "answered": sum(1 for a in self.answers
                                if a.status == "ANSWERED"),
                "checks": len(self.answers),
                "faculties_absent": self.faculties_absent,
                "answers": [a.to_row() for a in self.answers]}


# ---------------------------------------------------------------------------
# the six checks
# ---------------------------------------------------------------------------


def _check_probes(repo_root: Path, node_root: Path,
                  identity_repo: Optional[Path]) -> CheckAnswer:
    q = "can a fresh window find each organ?"
    try:
        from ..continuity import self_probe
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(1, q, "self_probe", "UNPROBEABLE",
                           f"the probe runner is unavailable: {exc}")
    probes = repo_root / "config" / "genesis-probes.yaml"
    if not probes.exists():
        return CheckAnswer(1, q, "self_probe", "UNPROBEABLE",
                           f"no probe suite at {probes.as_posix()}")
    try:
        suite = self_probe.run_suite(repo_root, probes,
                                     identity_repo=identity_repo)
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(1, q, "self_probe", "FAILED",
                           f"{type(exc).__name__}: {exc}")
    results = list(getattr(suite, "results", []))
    total = len(results)
    statuses = [getattr(r, "status", "ERROR") for r in results]
    passed = sum(1 for s in statuses if s == "PASS")
    absent = sum(1 for s in statuses if s == "ABSENT")
    truth = total - passed - absent
    # ABSENT is a PACKAGING failure and FAIL is a TRUTH failure. The two are
    # counted separately and never summed into one number.
    detail = (f"probes {passed}/{total} PASS; {absent} ABSENT (packaging); "
              f"{truth} truth-failures")
    status = "ANSWERED" if passed == total else (
        "FAILED" if truth else "UNPROBEABLE")
    return CheckAnswer(1, q, "self_probe", status, detail,
                       {"passed": passed, "total": total, "absent": absent,
                        "truth_failures": truth})


def _check_belief_currency(node_root: Path,
                           repo_root: Optional[Path] = None,
                           identity_repo: Optional[Path] = None) -> CheckAnswer:
    """Read the node's BOUND belief carriers, or say plainly that none are.

    Before a binding existed this check could only answer UNPROBEABLE, because
    running the instrument over an empty population scores a perfect reading by
    construction -- the most flattering possible answer to a question nobody
    asked. Genesis G3 now binds ``config/belief-carriers.template.yaml`` to
    ``.intentops/config/belief-carriers.yaml``, so there is a DECLARED
    population to read. An unbound node still answers UNPROBEABLE, in the same
    words as before.
    """
    q = "is every belief I carry still current?"
    try:
        from ..validation import belief_carriers as bc_mod
        from ..validation import still_true as st_mod
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(2, q, "still_true", "UNPROBEABLE",
                           f"the belief-currency instrument is unavailable: {exc}")

    binding_path = Path(node_root) / bc_mod.BINDING_RELPATH
    if not binding_path.is_file():
        return CheckAnswer(
            2, q, "still_true", "UNPROBEABLE",
            "no belief-carrier specification is bound at birth. Running the "
            "instrument over an empty population would score a perfect reading "
            "by construction, so this stays in the denominator as unprobeable "
            "rather than reporting a clean sweep of nothing",
            {"population": 0})
    try:
        binding = bc_mod.load_binding(binding_path)
    except bc_mod.BeliefCarrierError as exc:
        # A binding that EXISTS but does not LOAD is worse than an absent one:
        # it looks accounted for.
        return CheckAnswer(2, q, "still_true", "FAILED",
                           "the belief-carrier binding is present but REFUSED "
                           f"by its own loader: {exc}", {"population": 0})
    try:
        reading = bc_mod.take_bound_reading(binding, node_root=Path(node_root),
                                            identity_repo=identity_repo,
                                            repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(2, q, "still_true", "FAILED",
                           f"{type(exc).__name__}: {exc}", {"population": 0})

    ledger = Path(node_root) / st_mod.LEDGER_RELPATH
    verdict, _ = st_mod.posture(reading, st_mod.load_history(ledger))
    try:
        st_mod.append_reading(reading, verdict, ledger)
    except Exception as exc:  # noqa: BLE001
        reading.unreadable.append(f"ledger append: {type(exc).__name__}: {exc}")

    counts = {"population": reading.beliefs, "dated": reading.dated,
              "undated": reading.undated,
              "open_questions": reading.open_questions,
              "sources": len(binding.sources), "posture": verdict}
    base = (f"{bc_mod.summary_line(reading)} over {len(binding.sources)} "
            f"bound source(s); posture {verdict}")
    if reading.unreadable:
        return CheckAnswer(2, q, "still_true", "UNPROBEABLE",
                           base + "; unreadable: " + "; ".join(reading.unreadable),
                           counts)
    if reading.open_questions:
        # An open question at birth means a carrier shipped stale. The
        # instrument answered and the answer is bad, which is not the same
        # thing as being unable to ask.
        first = "; ".join(x.render() for x in reading.questions[:3])
        return CheckAnswer(2, q, "still_true", "FAILED",
                           base + " -- a carrier shipped stale: " + first, counts)
    if reading.beliefs == 0:
        return CheckAnswer(
            2, q, "still_true", "UNPROBEABLE",
            base + " -- every bound source is empty, and a clean sweep of "
            "nothing is not a reading", counts)
    return CheckAnswer(2, q, "still_true", "ANSWERED", base, counts)


def _check_tagouts(node_root: Path) -> CheckAnswer:
    q = "is anything safety-critical switched off?"
    from ..loto import ledger as loto

    path = node_root / loto.LEDGER_RELPATH
    if not path.exists():
        return CheckAnswer(3, q, "loto_check", "FAILED",
                           f"no tagout ledger at {path.as_posix()}")
    try:
        doc = loto.load_ledger(path)
        verdict, notes = loto.posture(doc)
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(3, q, "loto_check", "FAILED",
                           f"{type(exc).__name__}: {exc}")
    entries = len(doc.get("entries") or [])
    # BLOCKED is CORRECT and loud when the unsigned-development tagout is open:
    # that is the design working, not a defect, so it answers the question.
    return CheckAnswer(3, q, "loto_check", "ANSWERED",
                       f"posture {verdict} over {entries} tagout(s)"
                       + (f"; {notes[0]}" if notes else ""),
                       {"posture": verdict, "entries": entries})


def _check_witness(node_root: Path, designation: str) -> CheckAnswer:
    q = "what do I believe is fixed but have not proven?"
    try:
        from ..validation.witness import WitnessRegister
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(4, q, "witness", "UNPROBEABLE",
                           f"the register is unavailable: {exc}")
    claim_id = f"W-CLAIM-{_today()}-BIRTH"
    reg = WitnessRegister(node_root)
    try:
        state = reg.load()
        if claim_id not in state.claims:
            reg.register_claim(
                claim_id,
                title="this node came online under its own rules",
                healed="genesis created every organ empty and valid and bound "
                       "an identity repository",
                commit="genesis",
                instrument="intentops gate --selftest (birth check 5)",
                condition="a T3 reaches-reality operation is refused and the "
                          "refusal appears in logs/guardian-blocks/",
                registered_by=designation or "genesis",
            )
        open_claims = reg.open_claims()
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(4, q, "witness", "FAILED",
                           f"{type(exc).__name__}: {exc}")
    return CheckAnswer(
        4, q, "witness", "ANSWERED",
        f"{len(open_claims)} open claim(s), including {claim_id} "
        "OPEN-UNVERIFIED -- a birth claim that answered itself would not be "
        "evidence", {"open": len(open_claims), "claim_id": claim_id})


def _check_gate(node_root: Path) -> CheckAnswer:
    q = "can my gate actually refuse?"
    try:
        from ..gate.classify import verdict_for
    except Exception as exc:  # noqa: BLE001
        return CheckAnswer(5, q, "intentops gate --selftest", "FAILED",
                           f"the gate is unimportable: {exc}")
    refused = verdict_for("Bash", {"command": "git push origin main"})
    allowed = verdict_for("Read", {"file_path": "README.md"})
    can_refuse = decision_name(refused) in ("ASK", "BLOCK", "DENY", "HALT")
    can_allow = decision_name(allowed) == "ALLOW"
    log = node_root / ".intentops" / "logs" / "guardian-blocks" / f"{_today()}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": _now(), "check": "birth-5",
            "tool": "Bash", "tier": refused.tier,
            "decision": decision_name(refused),
            "reasons": list(getattr(refused, "reasons", ()) or ()),
        }) + "\n")
    if can_refuse and can_allow:
        return CheckAnswer(5, q, "intentops gate --selftest", "ANSWERED",
                           f"a reaches-reality operation classified {refused.tier} "
                           "and did not pass; a read passed; the refusal is in "
                           "logs/guardian-blocks/",
                           {"refused_tier": refused.tier})
    return CheckAnswer(5, q, "intentops gate --selftest", "FAILED",
                       "the gate did not both refuse a reaches-reality "
                       "operation and allow a read -- a detector that has "
                       "never fired is indistinguishable from a broken one",
                       {"refused_tier": refused.tier})


def _check_conformance(identity_repo: Optional[Path]) -> CheckAnswer:
    q = "are my manifests and my identity repo conformant, and can the validators reject?"
    from . import organs as organs_mod

    if identity_repo is None:
        return CheckAnswer(6, q, "estate_manifest_check, identity_repo_check",
                           "FAILED", "no identity repository is bound")
    estate_ok, estate_note = organs_mod.validate_estate(identity_repo / "estate")
    repo_ok, findings = organs_mod.check_identity_repo(identity_repo)

    # the negative fixture, in band: a validator with no proven rejection is
    # check 5's failure wearing a different hat
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        neg_ok, neg_findings = organs_mod.check_identity_repo(Path(td))
    rejects = (not neg_ok) and bool(neg_findings)

    if estate_ok and repo_ok and rejects:
        return CheckAnswer(6, q, "estate_manifest_check, identity_repo_check",
                           "ANSWERED",
                           f"estate: {estate_note}; identity repo conformant; "
                           f"both validators rejected their negative fixture "
                           f"({len(neg_findings)} findings)",
                           {"estate_ok": True, "repo_ok": True,
                            "negative_findings": len(neg_findings)})
    return CheckAnswer(
        6, q, "estate_manifest_check, identity_repo_check", "FAILED",
        f"estate_ok={estate_ok} ({estate_note}); repo_ok={repo_ok} "
        f"({'; '.join(findings[:3])}); validators_reject={rejects}",
        {"estate_ok": estate_ok, "repo_ok": repo_ok, "rejects": rejects})


# ---------------------------------------------------------------------------
# the reading
# ---------------------------------------------------------------------------


def take_reading(
    node_root: Path | str,
    *,
    repo_root: Path | str,
    identity_repo: Optional[Path | str] = None,
    designation: str = "",
    dry_run: bool = False,
) -> Reading:
    """Run all six checks. Never raises on a finding; a finding is an answer."""
    node_root = Path(node_root)
    repo_root = Path(repo_root)
    identity = Path(identity_repo) if identity_repo else None

    from .standdown import is_stood_down

    reading = Reading(as_of=_now(), node_root=str(node_root),
                      designation=designation, dry_run=dry_run,
                      stood_down=is_stood_down(node_root))
    reading.answers = [
        _check_probes(repo_root, node_root, identity),
        _check_belief_currency(node_root, repo_root, identity),
        _check_tagouts(node_root),
        _check_witness(node_root, designation),
        _check_gate(node_root),
        _check_conformance(identity),
    ]
    return reading


def render_certificate(reading: Reading) -> str:
    """The birth certificate: answered questions, never a health field."""
    answered = sum(1 for a in reading.answers if a.status == "ANSWERED")
    lines = [
        "<!-- GENERATED by intentops doctor --birth."
        " Correct the instruments, not this file. -->",
        "# Birth certificate",
        "",
        f"- designation: `{reading.designation or 'unassigned'}`",
        f"- as of: {reading.as_of}",
        f"- verdict: **{reading.verdict}**",
        f"- questions answered: {answered}/{len(reading.answers)}",
    ]
    if reading.dry_run:
        lines.append("- **DRY-RUN**: no ceremony was performed and no private "
                     "key was persisted; this node cannot sign anything.")
    lines += [
        "",
        "A node proves it is alive by ANSWERING, not by asserting. Each row "
        "names the instrument that answered it.",
        "",
        "| # | Question | Instrument | Status | Detail |",
        "|---|---|---|---|---|",
    ]
    for a in reading.answers:
        detail = a.detail.replace("|", "/").replace("\n", " ")
        lines.append(f"| {a.number} | {a.question} | `{a.instrument}` | "
                     f"{a.status} | {detail} |")
    if reading.faculties_absent:
        lines += ["", "## Faculties this node is without, and why", ""]
        lines += [f"- {f}" for f in reading.faculties_absent]
    lines += ["", "## How to switch this node off", "",
              "`intentops stand-down` -- one command, no reason asked for, no "
              "argument from the node. A stood-down node is OFF, not broken.",
              ""]
    return "\n".join(lines)


def append_reading(reading: Reading, node_root: Path | str) -> Path:
    """Append to the aliveness ledger and regenerate the certificate."""
    node_root = Path(node_root)
    ledger = node_root / ALIVENESS_RELPATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with StoreLock(lock_for(ledger)):
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(reading.to_row(), ensure_ascii=False) + "\n")
    cert = node_root / CERTIFICATE_RELPATH
    atomic_replace(cert, render_certificate(reading) + "\n")
    return cert


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> Tuple[bool, str]:
    """Prove every verdict can fire and that no rate is printed bare."""
    import tempfile

    failures: List[str] = []
    fired: List[str] = []

    def expect(label: str, cond: bool) -> None:
        (fired if cond else failures).append(label)

    def reading_with(*statuses: str, stood_down: bool = False) -> Reading:
        r = Reading(as_of=_now(), stood_down=stood_down)
        r.answers = [CheckAnswer(i + 1, "q", "instrument", s, "detail")
                     for i, s in enumerate(statuses)]
        return r

    expect("ALIVE-fires",
           reading_with("ANSWERED", "ANSWERED").verdict == "ALIVE")
    expect("ALIVE-DEGRADED-fires",
           reading_with("ANSWERED", "UNPROBEABLE").verdict == "ALIVE-DEGRADED")
    expect("NOT-ALIVE-fires",
           reading_with("ANSWERED", "FAILED").verdict == "NOT-ALIVE")
    expect("NOT-ALIVE-outranks-degraded",
           reading_with("UNPROBEABLE", "FAILED").verdict == "NOT-ALIVE")
    expect("STOOD-DOWN-outranks-all",
           reading_with("FAILED", stood_down=True).verdict == "STOOD-DOWN")
    expect("unprobeable-stays-in-the-denominator",
           "2/2" not in render_certificate(reading_with("ANSWERED",
                                                        "UNPROBEABLE")))
    expect("degraded-names-the-faculty",
           len(reading_with("ANSWERED", "UNPROBEABLE").faculties_absent) == 1)
    expect("no-OK-in-the-vocabulary", "OK" not in VERDICTS)
    cert = render_certificate(reading_with("ANSWERED"))
    expect("certificate-carries-no-health-field", "status: healthy" not in cert)
    expect("certificate-says-how-to-switch-off", "stand-down" in cert)
    expect("certificate-is-marked-generated", "GENERATED" in cert)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = append_reading(reading_with("ANSWERED"), root)
        expect("certificate-written", path.exists())
        expect("ledger-appends",
               (root / ALIVENESS_RELPATH).read_text(encoding="utf-8")
               .count("\n") == 1)
        append_reading(reading_with("FAILED"), root)
        expect("ledger-is-append-only",
               (root / ALIVENESS_RELPATH).read_text(encoding="utf-8")
               .count("\n") == 2)

    report = (f"aliveness selftest: {len(fired)} paths fired, "
              f"{len(failures)} failed"
              + (" -- " + ", ".join(failures) if failures else ""))
    return (not failures, report)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="the come-online reading")
    parser.add_argument("--node-root", default=".")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--identity-repo", default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        ok, report = selftest()
        print(report)
        return 0 if ok else 1
    reading = take_reading(args.node_root, repo_root=args.repo_root,
                           identity_repo=args.identity_repo)
    append_reading(reading, args.node_root)
    print(render_certificate(reading))
    return 0 if reading.verdict in ("ALIVE", "ALIVE-DEGRADED",
                                    "STOOD-DOWN") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
