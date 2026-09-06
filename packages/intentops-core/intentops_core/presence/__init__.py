"""Presence -- the surface where a node meets a person.

Four organs, in the order a message passes through them:

    :mod:`channels`        what an adapter must declare about its surface, and
                           the one shipped adapter (``NullChannel``) that
                           reaches nobody.
    :mod:`operator_rule`   *may this surface answer this speaker?* The node
                           answers only its bound operator; everyone else is
                           surfaced as an ask, never answered.
    :mod:`intents`         the taxonomy loader -- a closed vocabulary, or a halt.
    :mod:`router`          the deterministic front door: intent, tier, handler
                           name, reaches-reality; and one journal record per
                           message.
    :mod:`journal`         the append-only interaction journal, which holds
                           what happened and never what was said.

No module in this package makes a model call, imports a vendor SDK, or opens a
socket. The model is a declared seam (``router.Classifier``) whose shipped
implementation returns nothing.
"""

from __future__ import annotations

from .channels import (
    CHANNEL_CLASSES,
    ChannelAdapter,
    ChannelDescriptor,
    InboundMessage,
    NullChannel,
)
from .intents import (
    EscalationRule,
    IntentSpec,
    Taxonomy,
    TaxonomyHalt,
    load_taxonomy,
)
from .journal import (
    DISPOSITIONS,
    JOURNAL_RELPATH,
    InteractionJournal,
    InteractionRecord,
    JournalState,
)
from .operator_rule import (
    OPERATOR_RECORD_RELPATH,
    OperatorBinding,
    OperatorDecision,
    OperatorSlotMissing,
    OperatorVerdict,
    load_operator_binding,
)
from .router import (
    HANDLER_DISAMBIGUATE,
    HANDLER_IGNORE,
    HANDLER_SURFACE,
    UNCLASSIFIED,
    Classifier,
    NullClassifier,
    Route,
    route,
    route_inbound,
)

__all__ = [
    "CHANNEL_CLASSES",
    "ChannelAdapter",
    "ChannelDescriptor",
    "InboundMessage",
    "NullChannel",
    "EscalationRule",
    "IntentSpec",
    "Taxonomy",
    "TaxonomyHalt",
    "load_taxonomy",
    "DISPOSITIONS",
    "JOURNAL_RELPATH",
    "InteractionJournal",
    "InteractionRecord",
    "JournalState",
    "OPERATOR_RECORD_RELPATH",
    "OperatorBinding",
    "OperatorDecision",
    "OperatorSlotMissing",
    "OperatorVerdict",
    "load_operator_binding",
    "HANDLER_DISAMBIGUATE",
    "HANDLER_IGNORE",
    "HANDLER_SURFACE",
    "UNCLASSIFIED",
    "Classifier",
    "NullClassifier",
    "Route",
    "route",
    "route_inbound",
]
