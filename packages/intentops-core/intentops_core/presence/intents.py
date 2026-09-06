"""The intent taxonomy loader -- a closed vocabulary, or a halt.

PURPOSE
    Read ``config/intent-taxonomy.template.yaml`` (or an operator's own copy)
    and return a fully-validated :class:`Taxonomy`, or HALT with a remedy. The
    router consults this and nothing else to answer *what is this ask, what
    tier does it carry, who handles it, and does it reach outside the machine* --
    so this file is the single place that decides what a taxonomy MEANS. A
    second opinion living inside the router is how a node comes to disagree
    with its own configuration.

WRITE MODEL
    This module is a READER. It never writes a taxonomy file. The file is a
    single-writer store: the operator, by hand, one process at a time. There is
    no concurrent programmatic writer, so no lock is claimed here.

THE REFUSALS, AND WHY EACH ONE IS WORTH A HALT
    * A MISSING FILE halts. ``domains: {}`` is a valid, honest statement ("this
      node classifies nothing yet"); an absent file is a field nobody read, and
      the loader cannot tell that from packaging that dropped it.
    * An UNDECLARED KEY halts. ``reaches-reality`` instead of
      ``reaches_reality`` would otherwise load clean and route every action as
      local forever, and the typo is invisible because the missing field was
      quietly defaulted downstream.
    * A MISSING LOAD-BEARING FIELD halts, never defaults. There is no sensible
      default for "what tier does this carry" or "does this leave the machine".
    * A TIER OUTSIDE T0-T4 halts.
    * A ``reversible: true`` action above T0 with no ``rollback`` halts: a
      reversibility claim with no stated instrument is a promise nobody can
      keep.
    * A ``reversible: false`` action that names a rollback halts: the two
      statements contradict, and the contradiction favours acting.
    * An ESCALATION THAT LOWERS A TIER halts. Escalation raises; a rule that
      could lower is a rule that can walk an action under the gate.
    * An UNDECLARED ESCALATION MATCHER halts, and a rule with more than one
      matcher halts -- two matchers in one rule have no declared conjunction,
      so their meaning would be whatever the reader assumed.

BLIND SPOTS
    1. Every vocabulary here is a set of STRINGS. This module tells declared
       from undeclared; it cannot tell a plausible-but-wrong value (``T1`` on
       an action that in fact reaches production) from a correct one. Only a
       reviewer reads intent.
    2. ``match`` keywords are matched case-insensitively on word boundaries
       against raw text. That is a deliberate floor, not a classifier: it will
       miss paraphrase and it will fire on quotation. The router's answer to
       both is that ambiguity and absence route to the operator, never to a
       handler.
    3. Nothing here checks that a declared ``handler`` name exists anywhere.
       The router returns handler NAMES and resolves nothing, so a taxonomy
       cannot execute anything by being loaded -- but neither can this module
       tell you that a handler was never written.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "TIERS",
    "ESCALATION_MATCHERS",
    "TaxonomyHalt",
    "IntentSpec",
    "EscalationRule",
    "Taxonomy",
    "load_taxonomy",
    "tier_max",
    "selftest",
    "main",
]

#: Same ladder as ``gate.verdict.TIERS``; restated so this module can be read
#: (and tested) without importing the gate.
TIERS: Tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")
_TIER_ORDER: Dict[str, int] = {t: i for i, t in enumerate(TIERS)}

#: The closed matcher vocabulary. An escalation rule carries EXACTLY ONE.
ESCALATION_MATCHERS: Tuple[str, ...] = (
    "target_contains",
    "action_in",
    "target_outside_workspace",
)

_TOP_KEYS = {"schema", "as_of", "domains", "escalation"}
_DOMAIN_KEYS = {"description", "handler", "actions"}
_ACTION_REQUIRED = {"description", "tier", "reversible", "rollback",
                    "reaches_reality", "match"}
_ACTION_OPTIONAL = {"handler"}
_ESCALATION_KEYS = {"id", "when", "escalate_to", "reason"}

SCHEMA = "intent-taxonomy/v1"


class TaxonomyHalt(Exception):
    """The taxonomy could not be trusted. Carries a remedy, never a shrug."""


def tier_max(*tiers: str) -> str:
    """The strictest of the tiers given. Unknown values are a halt, not a max."""
    best = "T0"
    for t in tiers:
        if t not in _TIER_ORDER:
            raise TaxonomyHalt(f"tier {t!r} is outside {TIERS}")
        if _TIER_ORDER[t] > _TIER_ORDER[best]:
            best = t
    return best


@dataclass(frozen=True)
class IntentSpec:
    """One ``<domain>.<action>`` and everything the router needs about it."""

    intent_id: str
    domain: str
    action: str
    description: str
    tier: str
    reversible: bool
    rollback: Optional[str]
    reaches_reality: bool
    handler: str
    match: Tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        """Word-boundary, case-insensitive literal match. Deterministic."""
        lowered = text.lower()
        for keyword in self.match:
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, lowered):
                return True
        return False


@dataclass(frozen=True)
class EscalationRule:
    """One raise-only rule with exactly one declared matcher."""

    rule_id: str
    matcher: str
    operand: Any
    escalate_to: str
    reason: str

    def applies(self, *, intent: Optional[IntentSpec], target: str,
                inside_workspace: bool) -> bool:
        if self.matcher == "target_contains":
            lowered = target.lower()
            return any(str(needle).lower() in lowered for needle in self.operand)
        if self.matcher == "action_in":
            if intent is None:
                return False
            return intent.action in {str(a) for a in self.operand}
        if self.matcher == "target_outside_workspace":
            return bool(self.operand) and not inside_workspace
        raise TaxonomyHalt(  # pragma: no cover - construction refuses this
            f"undeclared escalation matcher {self.matcher!r}")


@dataclass(frozen=True)
class Taxonomy:
    """A validated taxonomy. Immutable once loaded."""

    schema: str
    as_of: str
    intents: Dict[str, IntentSpec]
    escalation: Tuple[EscalationRule, ...]
    source: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.intents)

    def get(self, intent_id: str) -> Optional[IntentSpec]:
        return self.intents.get(intent_id)

    def require(self, intent_id: str) -> IntentSpec:
        spec = self.intents.get(intent_id)
        if spec is None:
            raise TaxonomyHalt(
                f"intent {intent_id!r} is not declared in this taxonomy. "
                f"Declared ids: {', '.join(sorted(self.intents)) or '(none)'}"
            )
        return spec

    def candidates(self, text: str) -> Tuple[IntentSpec, ...]:
        """Every intent whose keywords match, in declaration order.

        Order is deterministic (declaration order, which is the file's order),
        so two runs over one text produce the same tuple.
        """
        return tuple(spec for spec in self.intents.values() if spec.matches(text))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "as_of": self.as_of,
            "population": len(self.intents),
            "intents": sorted(self.intents),
            "escalation": [r.rule_id for r in self.escalation],
        }


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _halt(message: str, remedy: str) -> "TaxonomyHalt":
    return TaxonomyHalt(f"{message}\n  remedy: {remedy}")


def _check_keys(where: str, present: Mapping[str, Any],
                allowed: set, required: set) -> None:
    undeclared = sorted(set(present) - allowed)
    if undeclared:
        raise _halt(
            f"intent taxonomy: {where} carries undeclared key(s) "
            f"{', '.join(repr(k) for k in undeclared)}",
            f"remove them, or declare them in {__name__}. An undeclared key is "
            "usually a typo for a load-bearing one, and a typo that loads "
            "clean is silent forever.")
    missing = sorted(required - set(present))
    if missing:
        raise _halt(
            f"intent taxonomy: {where} is missing required field(s) "
            f"{', '.join(repr(k) for k in missing)}",
            "declare them. A missing load-bearing field HALTS; there is no "
            "default for what an action costs or where it reaches.")


def load_taxonomy(path: Path | str) -> Taxonomy:
    """Load and fully validate a taxonomy file, or raise :class:`TaxonomyHalt`."""
    import yaml

    path = Path(path)
    if not path.is_file():
        raise _halt(
            f"intent taxonomy: no file at {path}",
            "ship config/intent-taxonomy.template.yaml, or point the loader at "
            "the operator's own copy. `domains: {}` is valid and means the node "
            "classifies nothing yet; a MISSING file is a field nobody read.")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _halt(f"intent taxonomy: {path} does not parse ({exc})",
                    "fix the YAML. A taxonomy that half-parses is not loaded "
                    "partially; it is not loaded.") from exc
    if not isinstance(raw, dict):
        raise _halt(f"intent taxonomy: {path} is not a mapping",
                    "the top level must be a mapping with schema, as_of, "
                    "domains and escalation.")

    _check_keys("the document", raw, _TOP_KEYS,
                {"schema", "as_of", "domains", "escalation"})
    if raw["schema"] != SCHEMA:
        raise _halt(
            f"intent taxonomy: schema is {raw['schema']!r}, expected {SCHEMA!r}",
            "this loader reads one schema version. A different version needs a "
            "migration, never a coerced read.")
    if not str(raw.get("as_of") or "").strip():
        raise _halt("intent taxonomy: as_of is empty",
                    "date the file. A belief carrier with no as-of has no "
                    "half-life and cannot be re-asked on time.")

    domains = raw["domains"]
    if domains is None:
        domains = {}
    if not isinstance(domains, dict):
        raise _halt("intent taxonomy: `domains` must be a mapping",
                    "use `domains: {}` for a node that classifies nothing yet.")

    intents: Dict[str, IntentSpec] = {}
    for domain_name, domain in domains.items():
        if not isinstance(domain, dict):
            raise _halt(f"intent taxonomy: domain {domain_name!r} is not a mapping",
                        "each domain is a mapping with description, handler and "
                        "actions.")
        _check_keys(f"domain {domain_name!r}", domain, _DOMAIN_KEYS, _DOMAIN_KEYS)
        actions = domain["actions"] or {}
        if not isinstance(actions, dict):
            raise _halt(f"intent taxonomy: {domain_name}.actions is not a mapping",
                        "actions is a mapping of action name to its declaration.")
        for action_name, action in actions.items():
            where = f"{domain_name}.{action_name}"
            if not isinstance(action, dict):
                raise _halt(f"intent taxonomy: {where} is not a mapping",
                            "each action is a mapping of its declared fields.")
            _check_keys(where, action, _ACTION_REQUIRED | _ACTION_OPTIONAL,
                        _ACTION_REQUIRED)
            tier = action["tier"]
            if tier not in TIERS:
                raise _halt(
                    f"intent taxonomy: {where}.tier is {tier!r}, outside {TIERS}",
                    "use a declared tier. An undeclared tier cannot be compared "
                    "against a ceiling, so it would be compared against nothing.")
            reversible = action["reversible"]
            if not isinstance(reversible, bool):
                raise _halt(
                    f"intent taxonomy: {where}.reversible is not a boolean",
                    "true or false. A string 'false' is truthy in most readers, "
                    "which is how a dry run executes for real.")
            reaches = action["reaches_reality"]
            if not isinstance(reaches, bool):
                raise _halt(
                    f"intent taxonomy: {where}.reaches_reality is not a boolean",
                    "true or false, declared per action. There is no default "
                    "for whether an action leaves the machine.")
            rollback = action["rollback"]
            if rollback is not None and not str(rollback).strip():
                rollback = None
            if reversible and tier != "T0" and not rollback:
                raise _halt(
                    f"intent taxonomy: {where} claims reversible: true at {tier} "
                    "but names no rollback",
                    "state HOW it is undone, or declare it irreversible. A "
                    "reversibility claim with no instrument is a promise nobody "
                    "can keep at the moment it is needed.")
            if not reversible and rollback:
                raise _halt(
                    f"intent taxonomy: {where} declares reversible: false and "
                    f"also names a rollback ({rollback!r})",
                    "the two statements contradict, and the contradiction "
                    "favours acting. Decide which is true.")
            match = action["match"]
            if match is None:
                match = []
            if not isinstance(match, list) or any(not isinstance(m, str) for m in match):
                raise _halt(f"intent taxonomy: {where}.match must be a list of strings",
                            "use [] for an intent that is reachable only by "
                            "declared id.")
            description = str(action["description"] or "").strip()
            if not description:
                raise _halt(f"intent taxonomy: {where}.description is empty",
                            "one line in the operator's language. A row nobody "
                            "described is a row nobody can review.")
            handler = str(action.get("handler") or domain["handler"] or "").strip()
            if not handler:
                raise _halt(f"intent taxonomy: {where} resolves to no handler",
                            "declare `handler` on the domain, or override it on "
                            "the action.")
            intents[f"{domain_name}.{action_name}"] = IntentSpec(
                intent_id=f"{domain_name}.{action_name}",
                domain=str(domain_name),
                action=str(action_name),
                description=description,
                tier=str(tier),
                reversible=bool(reversible),
                rollback=str(rollback) if rollback else None,
                reaches_reality=bool(reaches),
                handler=handler,
                match=tuple(match),
            )

    escalation_raw = raw["escalation"] or []
    if not isinstance(escalation_raw, list):
        raise _halt("intent taxonomy: `escalation` must be a list",
                    "use [] for a node with no escalation rules.")
    rules: List[EscalationRule] = []
    seen_ids: set = set()
    for entry in escalation_raw:
        if not isinstance(entry, dict):
            raise _halt("intent taxonomy: an escalation entry is not a mapping",
                        "each rule is a mapping with id, when, escalate_to and "
                        "reason.")
        _check_keys(f"escalation {entry.get('id')!r}", entry,
                    _ESCALATION_KEYS, _ESCALATION_KEYS)
        rule_id = str(entry["id"])
        if rule_id in seen_ids:
            raise _halt(f"intent taxonomy: duplicate escalation id {rule_id!r}",
                        "ids are how a rule is cited in a journal; two rules "
                        "with one id make the citation ambiguous.")
        seen_ids.add(rule_id)
        when = entry["when"]
        if not isinstance(when, dict) or len(when) != 1:
            raise _halt(
                f"intent taxonomy: escalation {rule_id!r} must carry EXACTLY ONE "
                "matcher",
                "two matchers in one rule have no declared conjunction, so "
                "their meaning would be whatever the reader assumed. Split the "
                "rule.")
        matcher, operand = next(iter(when.items()))
        if matcher not in ESCALATION_MATCHERS:
            raise _halt(
                f"intent taxonomy: escalation {rule_id!r} uses undeclared matcher "
                f"{matcher!r}",
                f"declared matchers: {', '.join(ESCALATION_MATCHERS)}. A prose "
                "condition is not a matcher -- no code can evaluate it, so it "
                "documents an intent nothing enforces.")
        escalate_to = entry["escalate_to"]
        if escalate_to not in TIERS:
            raise _halt(
                f"intent taxonomy: escalation {rule_id!r} escalates to "
                f"{escalate_to!r}, outside {TIERS}",
                "use a declared tier.")
        if escalate_to == "T0":
            raise _halt(
                f"intent taxonomy: escalation {rule_id!r} escalates to T0",
                "escalation RAISES. A rule that resolves to the lowest tier can "
                "only ever lower one, which is a rule for walking an action "
                "under the gate.")
        reason = str(entry["reason"] or "").strip()
        if not reason:
            raise _halt(f"intent taxonomy: escalation {rule_id!r} has no reason",
                        "state it. A refusal with no reason leaves the operator "
                        "nothing to act on.")
        if matcher in ("target_contains", "action_in"):
            if not isinstance(operand, list) or not operand:
                raise _halt(
                    f"intent taxonomy: escalation {rule_id!r} matcher {matcher} "
                    "needs a non-empty list",
                    "an empty list matches nothing and reads as a rule that is "
                    "on.")
        rules.append(EscalationRule(rule_id, matcher, operand,
                                    str(escalate_to), reason))

    return Taxonomy(schema=str(raw["schema"]), as_of=str(raw["as_of"]),
                    intents=intents, escalation=tuple(rules), source=path)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


_MINIMAL = """\
schema: intent-taxonomy/v1
as_of: "2026-09-06"
domains:
  file:
    description: "files"
    handler: filesystem
    actions:
      read:
        description: "read a file"
        tier: T0
        reversible: true
        rollback: null
        reaches_reality: false
        match: ["read"]
escalation: []
"""


def selftest() -> int:
    """Every halt above must be reachable, and the clean cases must still load."""
    import tempfile

    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def write(text: str, name: str = "t.yaml") -> Path:
            p = root / name
            p.write_text(text, encoding="utf-8")
            return p

        def halts(name: str, text: str) -> None:
            try:
                load_taxonomy(write(text))
                expect(name, False)
            except TaxonomyHalt:
                expect(name, True)

        tax = load_taxonomy(write(_MINIMAL))
        expect("clean-load", len(tax) == 1 and "file.read" in tax.intents)
        expect("keyword-match",
               tax.require("file.read").matches("please READ it") is True)
        expect("no-substring-false-positive",
               tax.require("file.read").matches("unreadable") is False)

        empty = load_taxonomy(write(
            'schema: intent-taxonomy/v1\nas_of: "2026-09-06"\n'
            'domains: {}\nescalation: []\n'))
        expect("empty-domains-is-valid", len(empty) == 0)

        try:
            load_taxonomy(root / "absent.yaml")
            expect("missing-file-halts", False)
        except TaxonomyHalt:
            expect("missing-file-halts", True)

        halts("undeclared-top-key",
              _MINIMAL + "surprise: 1\n")
        halts("undeclared-action-key",
              _MINIMAL.replace('        match: ["read"]',
                               '        match: ["read"]\n        reachesreality: true'))
        halts("missing-action-field",
              _MINIMAL.replace("        reaches_reality: false\n", ""))
        halts("bad-tier", _MINIMAL.replace("tier: T0", "tier: T9"))
        halts("non-boolean-reversible",
              _MINIMAL.replace("reversible: true", 'reversible: "false"'))
        halts("reversible-without-rollback",
              _MINIMAL.replace("tier: T0", "tier: T2"))
        halts("irreversible-with-rollback",
              _MINIMAL.replace("reversible: true", "reversible: false")
                      .replace("rollback: null", 'rollback: "undo it"'))
        halts("wrong-schema",
              _MINIMAL.replace("intent-taxonomy/v1", "intent-taxonomy/v2"))
        halts("empty-as_of", _MINIMAL.replace('as_of: "2026-09-06"', 'as_of: ""'))
        halts("no-handler",
              _MINIMAL.replace("    handler: filesystem\n", "    handler: ''\n"))
        halts("two-matchers", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      action_in: ["read"]\n'
            '      target_contains: ["x"]\n    escalate_to: T3\n    reason: "r"'))
        halts("undeclared-matcher", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      body_matches: ["x"]\n'
            '    escalate_to: T3\n    reason: "r"'))
        halts("escalation-to-T0", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      action_in: ["read"]\n'
            '    escalate_to: T0\n    reason: "r"'))
        halts("escalation-without-reason", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      action_in: ["read"]\n'
            '    escalate_to: T3\n    reason: ""'))
        halts("duplicate-escalation-id", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      action_in: ["read"]\n'
            '    escalate_to: T3\n    reason: "a"\n'
            '  - id: r\n    when:\n      action_in: ["read"]\n'
            '    escalate_to: T4\n    reason: "b"'))
        halts("empty-matcher-list", _MINIMAL.replace(
            "escalation: []",
            'escalation:\n  - id: r\n    when:\n      action_in: []\n'
            '    escalate_to: T3\n    reason: "r"'))

    total = 22
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} of {total}: "
              + ", ".join(failures))
        return 1
    print(f"SELFTEST PASS -- {total}/{total} paths behaved as declared")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the intent taxonomy loader")
    parser.add_argument("--path", default="config/intent-taxonomy.template.yaml")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    try:
        tax = load_taxonomy(args.path)
    except TaxonomyHalt as exc:
        print(f"HALT: {exc}")
        return 1
    print(f"{tax.schema} as of {tax.as_of}: {len(tax)} intents, "
          f"{len(tax.escalation)} escalation rules")
    for intent_id in sorted(tax.intents):
        spec = tax.intents[intent_id]
        reach = "reaches-reality" if spec.reaches_reality else "local"
        print(f"  {intent_id:<28} {spec.tier}  {reach:<15} -> {spec.handler}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
