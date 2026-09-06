"""Channel adapters -- the contract, and nothing that speaks a vendor's protocol.

PURPOSE
    Presence is where a node meets a person. A CHANNEL is one surface it can be
    met on: a terminal, a chat room, a mail thread, a voice loop. This module
    declares what an adapter for such a surface must be able to say about
    itself, and ships exactly one implementation -- :class:`NullChannel` --
    which reaches nobody.

    The framework ships no vendor adapters ON PURPOSE. A chat SDK in the core
    package would make every clone carry a dependency on a company it may never
    use, and the interesting part of an adapter is not its protocol but the
    three facts it must declare before the router will let it speak:

      * is this surface SHARED (other people can read it) or PRIVATE?
      * what is this node's OWN identity on it, so it can recognise its echo?
      * does sending on it reach outside this machine?

    Those three are what the operator rule and the router consume. Everything
    else -- websockets, tokens, retries, rate limits -- lives in an adapter
    package outside the core, exactly as a saddle owns its host's impurities.

WRITE MODEL
    None. This module holds no store and writes no file. Adapters are
    constructed by their owner and passed in. :class:`NullChannel` keeps sent
    messages in memory for tests and drops them when it is garbage collected.

BLIND SPOTS
    1. ``channel_class`` is the adapter's own CLAIM about its surface, never a
       discovered fact. An adapter that declares a group chat ``private``
       is believed, and the operator rule's decision will be wrong in exactly
       the direction that hurts. Nothing here can check it; the declaration is
       reviewed when the adapter is reviewed.
    2. ``self_ref`` is how a node recognises its own echo. An adapter that
       leaves it empty makes echo-suppression impossible, so the operator rule
       treats an empty ``self_ref`` as "cannot recognise myself" and never
       silently assumes a message is not its own.
    3. ``UNKNOWN`` is a real classification, not a missing one. It resolves
       stricter than ``shared`` everywhere it is read -- an unrecognised
       surface is the one you know least about.
    4. Nothing here rate-limits, retries, or bounds message size. An adapter
       that needs those owns them.
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "CHANNEL_CLASSES",
    "ChannelDescriptor",
    "InboundMessage",
    "ChannelAdapter",
    "NullChannel",
    "UndeclaredChannelClass",
    "selftest",
    "main",
]

#: Closed vocabulary. An adapter declaring anything else is refused at
#: construction -- a channel class nobody declared is a field nobody read.
CHANNEL_CLASSES: Tuple[str, ...] = ("private", "shared", "unknown")


class UndeclaredChannelClass(ValueError):
    """Raised when a descriptor carries a channel class outside the vocabulary."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ChannelDescriptor:
    """What a channel must be able to say about itself.

    ``channel_id``  a stable, opaque id for this surface. Not a display name,
                    and never a person's name.
    ``channel_class``  one of :data:`CHANNEL_CLASSES`.
    ``self_ref``    this node's own speaker reference on the surface, used to
                    recognise its own echo. Empty means "I cannot recognise
                    myself here", which is a stated limitation, not a default.
    ``reaches_reality``  True when sending here leaves this machine. A local
                    terminal is False; anything another person can receive is
                    True.
    """

    channel_id: str
    channel_class: str
    self_ref: str = ""
    reaches_reality: bool = True

    def __post_init__(self) -> None:
        if not self.channel_id:
            raise ValueError("a channel descriptor needs a channel_id")
        if self.channel_class not in CHANNEL_CLASSES:
            raise UndeclaredChannelClass(
                f"channel_class must be one of {CHANNEL_CLASSES}, got "
                f"{self.channel_class!r} -- an undeclared class is a hard exit, "
                "never a default of 'probably private'"
            )

    @property
    def is_shared(self) -> bool:
        """True for anything that is not PROVEN private.

        ``unknown`` answers True here on purpose: the stricter reading of a
        surface nobody classified is that other people can read it.
        """
        return self.channel_class != "private"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "channel_class": self.channel_class,
            "self_ref": self.self_ref,
            "reaches_reality": self.reaches_reality,
        }


@dataclass(frozen=True)
class InboundMessage:
    """One thing somebody said on a channel.

    ``speaker_ref`` is an opaque per-channel identity -- an account id, a
    handle, a terminal user. It is compared, never resolved: this module looks
    nothing up in a directory and knows no names.
    """

    channel_id: str
    speaker_ref: str
    text: str
    received_at: str = field(default_factory=_utcnow)
    thread_ref: str = ""
    declared_intent: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        if not self.channel_id:
            raise ValueError("an inbound message needs a channel_id")
        if not self.speaker_ref:
            raise ValueError(
                "an inbound message needs a speaker_ref -- an unattributed "
                "message cannot be tested against the operator record, and "
                "defaulting it to 'the operator' is the exact failure this "
                "layer exists to prevent"
            )


class ChannelAdapter(ABC):
    """The whole contract. Four methods, none of them optional.

    An adapter is trusted for what it DECLARES (its descriptor) and judged by
    what it DOES (poll/send). The router never calls ``send`` itself; it
    returns a route, and the caller sends. That seam is deliberate: a router
    that could speak is a router that can be made to speak by a classification
    bug.
    """

    @abstractmethod
    def descriptor(self) -> ChannelDescriptor:
        """The three facts above, plus this channel's id."""

    @abstractmethod
    def poll(self) -> Sequence[InboundMessage]:
        """Return messages received since the last poll. May be empty."""

    @abstractmethod
    def send(self, text: str, *, in_reply_to: Optional[InboundMessage] = None) -> str:
        """Emit ``text``. Returns an opaque message ref. Reaches reality."""

    @abstractmethod
    def close(self) -> None:
        """Release whatever the adapter holds. Idempotent."""

    # -- shared conveniences, deliberately not abstract --------------------

    @property
    def channel_id(self) -> str:
        return self.descriptor().channel_id


class NullChannel(ChannelAdapter):
    """The declared seam: an adapter that reaches nobody.

    It is the shipped default and the test double in one. ``send`` appends to
    an in-memory list and returns a ref; nothing leaves the process, which is
    why its descriptor declares ``reaches_reality=False`` unless the caller
    says otherwise. Tests that need to prove "the node would have spoken" read
    :attr:`sent`.
    """

    def __init__(
        self,
        channel_id: str = "null",
        *,
        channel_class: str = "private",
        self_ref: str = "node-self",
        reaches_reality: bool = False,
        inbox: Optional[Iterable[InboundMessage]] = None,
    ) -> None:
        self._descriptor = ChannelDescriptor(
            channel_id=channel_id,
            channel_class=channel_class,
            self_ref=self_ref,
            reaches_reality=reaches_reality,
        )
        self._inbox: List[InboundMessage] = list(inbox or ())
        self.sent: List[Tuple[str, Optional[str]]] = []
        self._closed = False

    def descriptor(self) -> ChannelDescriptor:
        return self._descriptor

    def poll(self) -> Sequence[InboundMessage]:
        drained, self._inbox = self._inbox, []
        return tuple(drained)

    def deliver(self, message: InboundMessage) -> None:
        """Test helper: put a message in this channel's inbox."""
        self._inbox.append(message)

    def send(self, text: str, *, in_reply_to: Optional[InboundMessage] = None) -> str:
        if self._closed:
            raise RuntimeError("NullChannel.send after close")
        self.sent.append((text, in_reply_to.thread_ref if in_reply_to else None))
        return f"null:{len(self.sent)}"

    def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Prove every refusal in this module can actually fire.

    A detector that has never fired is indistinguishable from a broken one.
    """
    failures: List[str] = []

    def expect(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    try:
        ChannelDescriptor("c", "semi-private")
        expect("undeclared-class-refused", False)
    except UndeclaredChannelClass:
        expect("undeclared-class-refused", True)

    try:
        ChannelDescriptor("", "private")
        expect("empty-id-refused", False)
    except ValueError:
        expect("empty-id-refused", True)

    try:
        InboundMessage("c", "", "hello")
        expect("unattributed-message-refused", False)
    except ValueError:
        expect("unattributed-message-refused", True)

    expect("unknown-reads-as-shared",
           ChannelDescriptor("c", "unknown").is_shared is True)
    expect("private-reads-as-not-shared",
           ChannelDescriptor("c", "private").is_shared is False)

    ch = NullChannel(inbox=[InboundMessage("null", "someone", "hi")])
    expect("poll-drains", len(ch.poll()) == 1 and len(ch.poll()) == 0)
    ch.send("out")
    expect("send-is-captured-not-emitted", ch.sent == [("out", None)])
    expect("null-channel-reaches-nobody",
           ch.descriptor().reaches_reality is False)
    ch.close()
    try:
        ch.send("after close")
        expect("send-after-close-refused", False)
    except RuntimeError:
        expect("send-after-close-refused", True)

    try:
        ChannelAdapter()  # type: ignore[abstract]
        expect("abc-cannot-be-instantiated", False)
    except TypeError:
        expect("abc-cannot-be-instantiated", True)

    total = 9
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} of {total}: "
              + ", ".join(failures))
        return 1
    print(f"SELFTEST PASS -- {total}/{total} paths behaved as declared")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="channel adapter contract")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    print("channel classes: " + ", ".join(CHANNEL_CLASSES))
    print("shipped adapters: NullChannel (reaches nobody)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
