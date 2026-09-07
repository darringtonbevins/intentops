"""The selftest registry -- one verb that runs every instrument's selftest.

The registry lives in :mod:`intentops_core.selftests.registry`; the
subprocess harness that calls one module's ``selftest()`` and prints a
delimited verdict frame lives in :mod:`intentops_core.selftests._child`.

This file deliberately imports NEITHER. ``python -m
intentops_core.selftests.registry`` executes the package's ``__init__``
first, so an eager re-export here means the module is already in
``sys.modules`` when ``runpy`` goes to execute it -- which Python reports as
``RuntimeWarning: ... this may result in unpredictable behaviour`` on every
single run, including the CI step. A convenience alias is not worth a
standing warning on the estate's own verb.
"""

from __future__ import annotations

__all__: list = []
