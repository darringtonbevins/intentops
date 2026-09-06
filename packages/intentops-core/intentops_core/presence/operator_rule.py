"""The operator rule -- a node answers its operator, and surfaces everyone else.

PURPOSE
    A node that speaks on a shared surface speaks in a voice its readers will
    take for its operator's. That is the whole danger, and it is not "the answer
    might be wrong". It is that **an unsupervised reply to a third party is an
    outbound communication the operator never authorised**, made in something
    very like their voice, in a place where other people read everything.

    So the invariant is not a filter on quality. It is a fence on authorship:

        The node answers only its bound operator. A message from anyone else
        is JOURNALED and SURFACED to the operator as an ask -- never answered
        autonomously.

    In the reference implementation this rule existed as one channel daemon's
    identity comparison plus a note in a memory file. Every other adapter that
    node grew inherited nothing, so the first stranger to address it on a new
    surface would have been answered. This module is that invariant as code, in
    one place, callable by every adapter, with a test that proves it can refuse.

THE THREE ANSWERS
    ``ANSWER``               the speaker IS the bound operator on this channel.
    ``SURFACE_TO_OPERATOR``  anyone else, and every uncertainty. The message
                             becomes an ask in the operator's queue; nothing is
                             said on the channel.
    ``IGNORE``               the node recognised its own echo. Reserved for
                             exactly that -- it is NOT a way to drop a message
                             the node found inconvenient, because a dropped
                             message the operator never sees is the same
                             silence as a crash.

FAIL-CLOSED, IN EVERY DIRECTION
    An unknown speaker, an unknown channel class, a channel the operator record
    names no identity for, an operator record with an empty identity list -- all
    of them resolve to ``SURFACE_TO_OPERATOR``. There is no path through this
    module that reaches ``ANSWER`` without a positive, declared identity match.

WRITE MODEL
    None. This module reads the operator record and returns a decision; it
    writes nothing and journals nothing itself. The router journals, so that
    the decision and its record are one step rather than two things that can
    drift apart.

    The operator record it reads is ``identity/operator/operator.yaml`` in the
    node's identity repository -- a locked fresh-read read-modify-write store
    owned by genesis, never written from here.

BLIND SPOTS
    1. Identity is an opaque per-channel STRING COMPARISON. If a channel lets
       one account impersonate another, this module cannot tell. It is not an
       authentication mechanism and must never be described as one; it decides
       who the node ANSWERS given an identity the adapter has already resolved.
    2. ``channel_class`` is the adapter's own claim (see
       :mod:`intentops_core.presence.channels` blind spot 1). A group surface
       misdeclared as private still never reaches ``ANSWER`` for a stranger --
       the identity check is what gates that -- but it will change how the
       reason reads.
    3. An empty ``self_ref`` means the node cannot recognise its own echo on
       that channel. This module says so in the reason string rather than
       assuming a message is not its own.
    4. The record's channel identities are exact strings. Case, whitespace and
       display-name changes are all differences; a renamed account reads as a
       stranger, which is the safe direction and the one that generates an ask.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .channels import ChannelDescriptor

__all__ = [
    "OPERATOR_RECORD_RELPATH",
    "OperatorVerdict",
    "OperatorDecision",
    "OperatorBinding",
    "OperatorSlotMissing",
    "load_operator_binding",
    "decide",
    "selftest",
    "main",
]

#: Where the operator record lives inside the identity repository. The genesis
#: skeleton creates it; nothing here creates one.
OPERATOR_RECORD_RELPATH = "identity/operator/operator.yaml"

_RECORD_SCHEMA = "operator/v1"


class OperatorSlotMissing(Exception):
    """The operator record is absent, unreadable, or does not declare a slot.

    Deliberately NOT recoverable by defaulting. A node that invents an operator
    is a node acting on a stranger's behalf, and the failure is silent because
    the invented operator answers every message perfectly happily.
    """


class OperatorVerdict(str, Enum):
    ANSWER = "ANSWER"
    SURFACE_TO_OPERATOR = "SURFACE_TO_OPERATOR"
    IGNORE = "IGNORE"


@dataclass(frozen=True)
class OperatorDecision:
    """The verdict plus the reason it was reached. A verdict with no reason
    leaves the operator nothing to act on."""

    verdict: OperatorVerdict
    reason: str
    speaker_is_operator: bool

    @property
    def may_answer(self) -> bool:
        return self.verdict is OperatorVerdict.ANSWER

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict.value, "reason": self.reason,
                "speaker_is_operator": self.speaker_is_operator}


@dataclass(frozen=True)
class OperatorBinding:
    """The node's answer to *who am I answerable to, and how do I recognise them*.

    ``channel_identities`` maps a channel id to the operator's identity
    references on it. At birth it is EMPTY, and an empty map is the honest
    state of a node whose operator has not yet told it how they appear
    anywhere -- so it answers nobody until they do.
    """

    label: str
    root_fingerprint: str
    channel_identities: Dict[str, Tuple[str, ...]]
    source: Optional[Path] = None

    def identities_for(self, channel_id: str) -> Tuple[str, ...]:
        return self.channel_identities.get(channel_id, ())

    @property
    def knows_no_channel(self) -> bool:
        return not any(self.channel_identities.values())


def load_operator_binding(identity_repo: Path | str) -> OperatorBinding:
    """Read the operator record, or raise :class:`OperatorSlotMissing`.

    Refuses, with a remedy, on: a missing repository, a missing record, an
    unparseable record, a wrong schema, and a record whose ``root_fingerprint``
    slot is empty. It does NOT refuse an empty ``channel_identities`` -- that is
    the birth state, and it is handled by never reaching ANSWER.
    """
    import yaml

    repo = Path(identity_repo)
    record = repo / OPERATOR_RECORD_RELPATH
    if not repo.is_dir():
        raise OperatorSlotMissing(
            f"no identity repository at {repo}\n"
            "  remedy: bind one (genesis phase G3 halts rather than creating a "
            "local one silently). Without it the node has no operator, and a "
            "node with no operator answers nobody.")
    if not record.is_file():
        raise OperatorSlotMissing(
            f"no operator record at {record}\n"
            "  remedy: run genesis, which writes the record with its slots "
            "empty. A missing record is a field nobody read; an empty slot is a "
            "true statement about a node whose operator has not answered yet.")
    try:
        raw = yaml.safe_load(record.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OperatorSlotMissing(
            f"operator record at {record} does not parse ({exc})\n"
            "  remedy: fix it. A half-read operator record is how a node comes "
            "to believe a stranger is its operator.") from exc
    if not isinstance(raw, dict):
        raise OperatorSlotMissing(
            f"operator record at {record} is not a mapping\n"
            "  remedy: it must carry schema, label, root_fingerprint and "
            "channel_identities.")
    if raw.get("schema") != _RECORD_SCHEMA:
        raise OperatorSlotMissing(
            f"operator record schema is {raw.get('schema')!r}, expected "
            f"{_RECORD_SCHEMA!r}\n"
            "  remedy: migrate the record. This reader reads one version and "
            "coerces nothing.")
    fingerprint = str(raw.get("root_fingerprint") or "").strip()
    if not fingerprint:
        raise OperatorSlotMissing(
            f"operator record at {record} declares no root_fingerprint\n"
            "  remedy: complete the key ceremony (genesis phase G2). The "
            "fingerprint is what ties this record to a key somebody actually "
            "holds; without it the record names nobody.")

    identities_raw = raw.get("channel_identities") or {}
    if not isinstance(identities_raw, dict):
        raise OperatorSlotMissing(
            f"operator record at {record}: channel_identities is not a mapping\n"
            "  remedy: map each channel id to a list of the operator's "
            "references on it, or leave it empty.")
    identities: Dict[str, Tuple[str, ...]] = {}
    for channel_id, refs in identities_raw.items():
        if isinstance(refs, str):
            refs = [refs]
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            raise OperatorSlotMissing(
                f"operator record at {record}: channel_identities"
                f"[{channel_id!r}] is not a list of strings\n"
                "  remedy: a list of opaque identity references, exactly as the "
                "adapter reports them.")
        identities[str(channel_id)] = tuple(r for r in refs if r.strip())

    return OperatorBinding(
        label=str(raw.get("label") or ""),
        root_fingerprint=fingerprint,
        channel_identities=identities,
        source=record,
    )


def decide(
    binding: OperatorBinding,
    descriptor: ChannelDescriptor,
    speaker_ref: str,
) -> OperatorDecision:
    """*May this surface answer this speaker?* Pure; reads nothing from disk.

    The order of the checks is load-bearing: echo first (a node must not treat
    its own output as an ask), then the positive identity match, then every
    remaining case -- all of which surface.
    """
    if not speaker_ref:
        return OperatorDecision(
            OperatorVerdict.SURFACE_TO_OPERATOR,
            "the message carries no speaker reference, so it cannot be tested "
            "against the operator record",
            False)

    self_ref = descriptor.self_ref
    if self_ref and speaker_ref == self_ref:
        return OperatorDecision(
            OperatorVerdict.IGNORE,
            f"the speaker is this node's own reference on {descriptor.channel_id}",
            False)

    known = binding.identities_for(descriptor.channel_id)
    if speaker_ref in known:
        return OperatorDecision(
            OperatorVerdict.ANSWER,
            f"the speaker is the operator's declared identity on "
            f"{descriptor.channel_id}",
            True)

    if not known:
        detail = ("the operator record names no identity for "
                  f"{descriptor.channel_id}")
        if binding.knows_no_channel:
            detail += (" -- and none for any channel, which is the birth state: "
                       "the node has not been told how its operator appears "
                       "anywhere, so it answers nobody")
        return OperatorDecision(OperatorVerdict.SURFACE_TO_OPERATOR, detail, False)

    where = ("a shared surface" if descriptor.is_shared
             else "a surface declared private")
    return OperatorDecision(
        OperatorVerdict.SURFACE_TO_OPERATOR,
        f"the speaker is not the operator's declared identity on "
        f"{descriptor.channel_id} ({where}); answering would be an outbound "
        "communication the operator never authorised, in something very like "
        "their voice",
        False)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


_RECORD = """\
schema: operator/v1
as_of: "2026-09-06"
label: null
root_fingerprint: "fp-placeholder"
notification_posture: null
off_limits: []
channel_identities:
  chan-a: ["op-on-a"]
"""


def selftest() -> int:
    """Every refusal must fire, and ANSWER must still be reachable."""
    import tempfile

    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "identity-repo"
        (repo / "identity" / "operator").mkdir(parents=True)
        record = repo / OPERATOR_RECORD_RELPATH

        try:
            load_operator_binding(repo)
            expect("missing-record-halts", False)
        except OperatorSlotMissing:
            expect("missing-record-halts", True)

        try:
            load_operator_binding(Path(tmp) / "nowhere")
            expect("missing-repo-halts", False)
        except OperatorSlotMissing:
            expect("missing-repo-halts", True)

        record.write_text(_RECORD.replace('root_fingerprint: "fp-placeholder"',
                                          'root_fingerprint: ""'),
                          encoding="utf-8")
        try:
            load_operator_binding(repo)
            expect("empty-fingerprint-halts", False)
        except OperatorSlotMissing:
            expect("empty-fingerprint-halts", True)

        record.write_text(_RECORD.replace("operator/v1", "operator/v2"),
                          encoding="utf-8")
        try:
            load_operator_binding(repo)
            expect("wrong-schema-halts", False)
        except OperatorSlotMissing:
            expect("wrong-schema-halts", True)

        record.write_text(_RECORD, encoding="utf-8")
        binding = load_operator_binding(repo)
        expect("clean-load", binding.identities_for("chan-a") == ("op-on-a",))

        shared = ChannelDescriptor("chan-a", "shared", self_ref="me")
        private = ChannelDescriptor("chan-a", "private", self_ref="me")
        unknown_channel = ChannelDescriptor("chan-z", "unknown", self_ref="me")

        expect("operator-is-answered",
               decide(binding, shared, "op-on-a").verdict is OperatorVerdict.ANSWER)
        expect("stranger-is-surfaced-on-shared",
               decide(binding, shared, "someone-else").verdict
               is OperatorVerdict.SURFACE_TO_OPERATOR)
        expect("stranger-is-surfaced-on-private",
               decide(binding, private, "someone-else").verdict
               is OperatorVerdict.SURFACE_TO_OPERATOR)
        expect("unmapped-channel-surfaces",
               decide(binding, unknown_channel, "op-on-a").verdict
               is OperatorVerdict.SURFACE_TO_OPERATOR)
        expect("own-echo-is-ignored",
               decide(binding, shared, "me").verdict is OperatorVerdict.IGNORE)
        expect("empty-speaker-surfaces",
               decide(binding, shared, "").verdict
               is OperatorVerdict.SURFACE_TO_OPERATOR)
        expect("verdicts-carry-a-reason",
               all(decide(binding, shared, ref).reason
                   for ref in ("op-on-a", "stranger", "me", "")))

        birth = OperatorBinding("", "fp", {}, None)
        d = decide(birth, shared, "anybody")
        expect("birth-state-answers-nobody",
               d.verdict is OperatorVerdict.SURFACE_TO_OPERATOR
               and "birth state" in d.reason)

        # the identity comparison is exact -- a near miss is a stranger
        expect("near-miss-is-a-stranger",
               decide(binding, shared, "Op-On-A").verdict
               is OperatorVerdict.SURFACE_TO_OPERATOR)

    total = 15
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} of {total}: "
              + ", ".join(failures))
        return 1
    print(f"SELFTEST PASS -- {total}/{total} paths behaved as declared")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="the operator rule: who may this node answer")
    parser.add_argument("--identity-repo", default=None)
    parser.add_argument("--channel", default="")
    parser.add_argument("--channel-class", default="unknown")
    parser.add_argument("--speaker", default="")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest()
    if not args.identity_repo:
        print("HALT: --identity-repo is required. There is no default operator.")
        return 1
    try:
        binding = load_operator_binding(args.identity_repo)
    except OperatorSlotMissing as exc:
        print(f"HALT: {exc}")
        return 1
    if not args.channel:
        print(f"operator root {binding.root_fingerprint}")
        for channel_id in sorted(binding.channel_identities):
            count = len(binding.channel_identities[channel_id])
            print(f"  {channel_id}: {count} declared identity reference(s)")
        if binding.knows_no_channel:
            print("  (no channel identities declared -- this node answers nobody)")
        return 0
    decision = decide(binding,
                      ChannelDescriptor(args.channel, args.channel_class),
                      args.speaker)
    print(f"{decision.verdict.value}: {decision.reason}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
