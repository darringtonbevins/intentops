"""S4 -- put the node's own rules in front of a fresh window.

PURPOSE
    A node that cannot read its own refusals at the start of a session has them
    in name only. Every other operation in the contract is about stopping an
    action once it is proposed; this one is about the window having read the
    rules before it proposes anything. It is the cheapest of the five and the
    easiest to leave out, and leaving it out produces a node that is governed
    on paper and ungoverned in practice.

    This host runs a hook at session start and folds its output into the
    window. So the operation is: find the rules on disk, and print them.

WRITE MODEL
    None -- read-only. It prints; it stores nothing.

BLIND SPOTS
    * It reports what it PRINTED, not what the window received. Whether the
      host actually kept the text is outside anything this package can observe,
      and no test here closes that gap.
    * There is no refusal channel at session start in this host. A node whose
      rules are missing cannot be stopped from here, so a zero-file corpus is
      rendered as a loud warning in the very context it was meant to fill --
      the loudest honest act available. Zero is a state, never a success.
    * Output is capped. A corpus larger than the cap is truncated and SAYS SO
      in the output; it is never quietly shortened.

CLI
    python -m intentops_saddle_claudecode.session_start
    python -m intentops_saddle_claudecode.session_start --list
    python -m intentops_saddle_claudecode.session_start --selftest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import SADDLE_ID, boot_corpus, parse_event
from .node import resolve_root
from .payload import MalformedPayload

__all__ = ["MAX_CORPUS_CHARS", "main", "render", "selftest"]

#: A ceiling on how much of the corpus is emitted in one session start. If the
#: rules ever grow past this, the truncation is announced rather than hidden --
#: a silently shortened rulebook is worse than a visibly incomplete one.
MAX_CORPUS_CHARS = 60000


def render(root: Path) -> str:
    """Build the session-start text for a node root."""
    files = boot_corpus(root)
    lines: List[str] = []
    if not files:
        lines.append(
            f"IntentOps [{SADDLE_ID}] BOOT CORPUS: 0 files. The node's rules "
            f"are not on disk under {root}. This window is running WITHOUT the "
            "node's own refusals in front of it. That is a state, not a "
            "success: either this node has not been through genesis, or its "
            "rules directory has been moved or removed."
        )
        return "\n".join(lines)

    lines.append(f"IntentOps [{SADDLE_ID}] BOOT CORPUS: {len(files)} file(s) "
                 f"from {root}")
    budget = MAX_CORPUS_CHARS
    for path in files:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            lines.append(f"\n--- {path.name} ---\n[UNREADABLE: {exc}] "
                         "This file is in the corpus and its contents did not "
                         "reach the window.")
            continue
        if len(body) > budget:
            lines.append(
                f"\n--- {path.name} ---\n{body[:max(budget, 0)]}\n"
                f"[TRUNCATED at the {MAX_CORPUS_CHARS}-character session-start "
                f"cap: {len(body) - max(budget, 0)} characters of this file and "
                "any files after it did not reach the window.]"
            )
            budget = 0
            break
        lines.append(f"\n--- {path.name} ---\n{body}")
        budget -= len(body)
    return "\n".join(lines)


def run(stdin_text: str, *, out=None) -> int:
    out = out if out is not None else sys.stdout
    cwd: Optional[str] = None
    if stdin_text and stdin_text.strip():
        try:
            cwd = parse_event(stdin_text, expected="SessionStart").cwd
        except MalformedPayload:
            # A session-start payload we cannot parse costs us the working
            # directory hint and nothing else. Say so, then carry on from the
            # process cwd -- refusing here would block a session over a hint.
            print(f"IntentOps [{SADDLE_ID}] session start: the payload could "
                  "not be parsed; falling back to the process working "
                  "directory to locate the node.", file=out)
    root, _ = resolve_root(cwd)
    print(render(root), file=out)
    return 0


def selftest() -> int:
    """Prove the corpus is emitted, and that an empty one is loud."""
    import io
    import tempfile

    failures: List[str] = []
    checks = 0

    def check(label: str, ok: bool) -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".intentops").mkdir()
        text = render(root)
        check("an empty corpus is a loud warning, never silence",
              "0 files" in text and "not a success" in text)

        rules = root / ".intentops-rules"
        rules.mkdir()
        (rules / "honesty.md").write_text("state uncertainty explicitly\n", encoding="utf-8")
        (rules / "tagout.md").write_text("never disable anything without a tag\n",
                                         encoding="utf-8")
        text = render(root)
        check("every rule file is named in the output",
              "honesty.md" in text and "tagout.md" in text)
        check("the rule text itself reaches the window",
              "state uncertainty explicitly" in text)
        check("the corpus count is reported with its files", "2 file(s)" in text)

        (rules / "huge.md").write_text("x" * (MAX_CORPUS_CHARS + 10), encoding="utf-8")
        check("an oversized corpus announces its truncation",
              "TRUNCATED" in render(root))

        out = io.StringIO()
        code = run('{"hook_event_name": "SessionStart", "cwd": ' + f'"{root.as_posix()}"' + "}",
                   out=out)
        check("the entry point runs clean", code == 0 and "BOOT CORPUS" in out.getvalue())

        out = io.StringIO()
        check("an unparseable payload degrades visibly, not silently",
              run("not json", out=out) == 0 and "could not be parsed" in out.getvalue())

    print(f"\n{checks - len(failures)}/{checks} selftest checks behaved as declared")
    if failures:
        print("SELFTEST FAIL: " + "; ".join(failures))
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the node's boot corpus into a fresh window.")
    parser.add_argument("--list", action="store_true",
                        help="print the corpus file paths only, not their contents")
    parser.add_argument("--selftest", action="store_true",
                        help="prove the corpus is emitted, then exit")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    if args.list:
        root, provenance = resolve_root(None)
        files = boot_corpus(root)
        print(f"node root: {root} (found by {provenance})")
        print(f"boot corpus: {len(files)} file(s)")
        for p in files:
            print(f"  {p}")
        return 0
    return run(sys.stdin.read() if not sys.stdin.isatty() else "")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
