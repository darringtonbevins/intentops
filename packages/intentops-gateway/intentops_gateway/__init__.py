"""The IntentOps gateway -- one seam, authenticated, default-deny.

PURPOSE
    Every tool call a harness makes should pass one place that can classify it,
    record it, and refuse it. That place is this package. It is STATELESS
    between requests: nothing is remembered across calls except what lands in
    the append-only request ledger, so there is no session to hijack and no
    trust that accumulates because a caller has been talking for a while.

    THE TWO CONTROLS THAT SHIPPED DISABLED UPSTREAM ARE ON HERE, AND FIRST.
    The reference estate this generalises from ran a gateway whose caller
    identity resolved to the literal string "anonymous" BY CONSTRUCTION, and
    whose two safety controls -- harness authentication and the per-harness
    backend allowlist -- both shipped behind environment flags that were never
    flipped. In this package:

      1. authentication is unconditional. No token is 401. There is no
         "anonymous" identity to fall through to, and no environment variable
         that opens the door.
      2. the backend allowlist is default-deny. A harness with no grant reaches
         no backend, and an unreadable policy refuses everything rather than
         serving blind.
      3. an UNTAGGED tool is T4 and DENIED, never T2. A tool nobody classified
         is a tool nobody bounded (the public default-deny ruling, R-D2).

WHAT IS HERE
    ``tokens``    the bearer credential: digest-only, minted once, rotatable
    ``ledger``    the append-only record of every request, refusals included
    ``backends``  the backend registry (from the estate) and the allowlist
    ``tiers``     per-call classification into a core ``Verdict``
    ``boot``      the read-only boot image a cold client asks for first
    ``tools``     the closed built-in tool surface and the JSON-RPC dispatch
    ``server``    JSON-RPC over HTTP, stdlib only, loopback by default
    ``mcp``       the same tools over MCP stdio, reusing the saddle's protocol
    ``cli``       ``intentops gateway serve|status|token rotate``

BLIND SPOTS (whole-package; each module states its own as well)
    * The gateway computes and records verdicts. It cannot refuse a call a
      harness never routes through it. That gap is the same S2 limitation the
      MCP saddle states, and it is reported in ``intentops.status`` rather
      than papered over.
    * A bearer token over cleartext HTTP is theatre. The default binding is
      loopback for exactly that reason; any non-loopback binding owes a TLS
      terminator in front of it, and nothing here can check that one exists.
    * Nothing here rate-limits. Loopback plus 256 bits of entropy is the whole
      defence against guessing.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
