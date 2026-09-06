"""Host adapters: one charter, N scheduling artifacts, rendered not authored.

PURPOSE
    Turn a validated :class:`~intentops_core.loops.charters.Charter` into the
    scheduling artifact one host actually understands -- a cron line, a systemd
    timer plus its service unit, or a Windows scheduled-task XML. The charter
    is the source of truth; these renderers are the only writers of a host
    schedule. Two hand-authored schedules on two hosts drift and nothing
    notices; one charter and N renderers cannot drift from each other, and
    ``intentops loops check`` catches the case where a human edited a rendered
    artifact anyway.

    THE ADAPTERS REFUSE RATHER THAN ROUND. Classic cron cannot express "every
    7 minutes": ``*/7`` restarts its count at each hour and fires wrong four
    times a day. The cron adapter therefore raises :class:`AdapterError` naming
    the charter, instead of emitting a line that is subtly and silently early.
    A host that cannot express a cadence exactly is a fact the operator should
    learn at render time, not from a loop that ran at the wrong hour for a
    month.

    THE WINDOWS ACTION IS NEVER A BARE CONSOLE ENGINE. A scheduled task whose
    command is a shell or interpreter executable, under an interactive logon,
    flashes a console window on the operator's desktop at every fire, and a
    hide flag cannot suppress it -- the console host is created before the
    interpreter parses its own flags. The action is therefore always
    ``wscript`` plus a windowless wrapper script, and this adapter REFUSES a
    wrapper that is itself one of those engines.

    EVERY RENDER PINS A WORKING DIRECTORY. A scheduled job's working directory
    is routinely empty and then inherits the system directory, which turns
    every relative path in the job into a path that does not exist. Each
    renderer sets it explicitly.

WRITE MODEL
    None. Every function here is PURE: charter plus context in, text out. No
    file is written, no scheduler is contacted, no clock is read -- the start
    boundary is a field on the context precisely so two renders of the same
    charter are byte-identical and a diff against the installed artifact means
    something. Installing what is rendered is the operator's own act, and is
    the moment a host binding is created.

BLIND SPOTS
    * These renderers produce TEXT. Nothing here proves the text was installed,
      that the host accepted it, or that it ever fired. That is
      :mod:`intentops_core.loops.conformance`'s question and it needs the host
      to be asked.
    * Times are LOCAL to whatever host renders and installs. A charter that
      says ``03:15`` fires at two different instants on two hosts in two zones
      and no adapter can see that.
    * The cron line assumes a POSIX shell and a daemon reading the classic
      five-field format. It is not validated against one implementation's
      extensions.
    * The Windows XML is schema-shaped and well-formed, and is NOT validated
      against the Task Scheduler schema itself -- only the scheduler can do
      that, by importing it. It is emitted as text; save it as UTF-16 if the
      host's import tool requires that.
    * Nothing here checks that ``tick_command`` exists on the host. A perfectly
      rendered timer for a command that is not installed fails at the first
      fire, loudly, which is the correct direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape as _xml_escape

from .charters import Cadence, Charter, CharterError

__all__ = [
    "AdapterError",
    "HostContext",
    "Binding",
    "ADAPTER_KINDS",
    "render_cron",
    "render_systemd",
    "render_windows_task",
    "render",
    "render_all",
    "selftest",
]

#: The host kinds this seed can render. An undeclared kind is a hard exit.
ADAPTER_KINDS: Tuple[str, ...] = ("cron", "systemd", "windows-task")

#: Wrapper leaf names that are shells or interpreters. A Windows task action
#: must never be one of these directly -- see the module docstring.
_CONSOLE_ENGINES: Tuple[str, ...] = (
    "powershell.exe", "pwsh.exe", "cmd.exe", "python.exe", "pythonw.exe",
    "conhost.exe", "bash.exe", "sh.exe", "wsl.exe",
)

_WEEKDAY_CRON = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5,
                 "sat": 6}
_WEEKDAY_SYSTEMD = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu",
                    "fri": "Fri", "sat": "Sat", "sun": "Sun"}
_WEEKDAY_WINDOWS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
                    "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
                    "sun": "Sunday"}
_TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_QUOTE_ENTITY = "&" + "quot;"


class AdapterError(CharterError):
    """A host cannot express this charter EXACTLY. Carries the remedy."""


@dataclass(frozen=True)
class HostContext:
    """Everything host-specific a render needs, and nothing charter-specific.

    ``start_date`` is a field rather than today's date on purpose: a renderer
    that reads a clock produces a different artifact every run, and then a diff
    against the installed schedule can never mean "somebody edited this".
    """

    node_root: str
    tick_command: str
    user: str | None = None
    windows_wrapper: str | None = None
    start_date: str = "2026-01-01"
    log_path: str | None = None

    def command_for(self, charter: Charter) -> str:
        return f"{self.tick_command} {charter.id}"


@dataclass(frozen=True)
class Binding:
    """A rendered schedule for one charter on one host kind."""

    kind: str
    charter_id: str
    cadence: str
    files: Dict[str, str] = field(default_factory=dict)

    def single_text(self) -> str:
        """The one artifact, when this kind renders exactly one."""
        if len(self.files) != 1:
            raise AdapterError(
                f"{self.kind} rendered {len(self.files)} artifacts",
                "read .files directly; this kind is not single-artifact")
        return next(iter(self.files.values()))


def _refuse(charter: Charter, host: str, why: str, remedy: str) -> AdapterError:
    return AdapterError(f"{host} cannot express this charter exactly: {why}",
                        remedy, subject=charter.id)


# --------------------------------------------------------------------------
# cron
# --------------------------------------------------------------------------


def _cron_expression(charter: Charter) -> str:
    cadence = charter.cadence
    if cadence.kind == "manual":
        raise _refuse(charter, "cron", "the cadence is manual",
                      "a manual charter has no schedule to render; run it by "
                      "hand or give it a cadence")
    if cadence.kind == "interval":
        n, unit = cadence.interval_parts()
        if unit == "m":
            if n == 60:
                return "0 * * * *"
            if n > 60 or 60 % n:
                raise _refuse(
                    charter, "cron", f"a {n}-minute interval does not divide "
                    "the hour, so the step field restarts its count every hour "
                    "and fires early",
                    "choose a minute interval that divides 60 (1, 2, 3, 4, 5, "
                    "6, 10, 12, 15, 20, 30, 60), or bind this charter on a "
                    "host whose scheduler expresses true intervals")
            return f"*/{n} * * * *"
        if unit == "h":
            if n == 24:
                return "0 0 * * *"
            if n > 24 or 24 % n:
                raise _refuse(
                    charter, "cron", f"a {n}-hour interval does not divide the "
                    "day, so the step field restarts its count every day and "
                    "fires early",
                    "choose an hour interval that divides 24 (1, 2, 3, 4, 6, "
                    "8, 12, 24), or bind this charter on a host whose "
                    "scheduler expresses true intervals")
            return f"0 */{n} * * *"
        if n != 1:
            raise _refuse(
                charter, "cron", f"cron has no {n}-day interval field",
                "use a daily cadence, or bind this charter on a host whose "
                "scheduler expresses a day interval (systemd and the Windows "
                "scheduler both do)")
        return "0 0 * * *"
    hh, mm = cadence.at_parts()
    if cadence.kind == "daily":
        return f"{mm} {hh} * * *"
    return f"{mm} {hh} * * {_WEEKDAY_CRON[str(cadence.weekday)]}"


def render_cron(charter: Charter, context: HostContext) -> Binding:
    """One crontab line, with its charter named in a comment above it."""
    expression = _cron_expression(charter)
    redirect = f" >> {context.log_path} 2>&1" if context.log_path else ""
    line = (f"{expression} cd {context.node_root} && "
            f"{context.command_for(charter)}{redirect}")
    header = (f"# intentops loop charter: {charter.id} "
              f"({charter.cadence.describe()}, ceiling {charter.tier_ceiling})\n"
              f"# purpose: {charter.purpose}\n"
              f"# kill switch: {charter.kill_switch}\n")
    return Binding(kind="cron", charter_id=charter.id, cadence=expression,
                   files={f"intentops-loop-{charter.id}.cron":
                          header + line + "\n"})


# --------------------------------------------------------------------------
# systemd
# --------------------------------------------------------------------------


def _systemd_duration(cadence: Cadence) -> str:
    n, unit = cadence.interval_parts()
    return {"m": f"{n}min", "h": f"{n}h", "d": f"{n}d"}[unit]


def _systemd_schedule(charter: Charter) -> Tuple[str, str]:
    """(the timer's schedule block, a one-line description of it)."""
    cadence = charter.cadence
    if cadence.kind == "manual":
        raise _refuse(charter, "systemd", "the cadence is manual",
                      "a manual charter has no schedule to render")
    if cadence.kind == "interval":
        every = _systemd_duration(cadence)
        return (f"OnBootSec={every}\nOnUnitActiveSec={every}\n",
                f"OnUnitActiveSec={every}")
    hh, mm = cadence.at_parts()
    if cadence.kind == "daily":
        calendar = f"*-*-* {hh:02d}:{mm:02d}:00"
    else:
        day = _WEEKDAY_SYSTEMD[str(cadence.weekday)]
        calendar = f"{day} *-*-* {hh:02d}:{mm:02d}:00"
    return (f"OnCalendar={calendar}\nPersistent=true\n",
            f"OnCalendar={calendar}")


def render_systemd(charter: Charter, context: HostContext) -> Binding:
    """A service unit and its timer. Type=oneshot: a loop tick ends."""
    schedule, described = _systemd_schedule(charter)
    unit = f"intentops-loop-{charter.id}"
    user_line = f"User={context.user}\n" if context.user else ""
    service = (
        "[Unit]\n"
        f"Description=IntentOps loop charter {charter.id} -- {charter.purpose}\n"
        "Documentation=intentops loops check\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"{user_line}"
        f"WorkingDirectory={context.node_root}\n"
        f"ExecStart={context.command_for(charter)}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=IntentOps loop charter {charter.id} "
        f"({charter.cadence.describe()}, ceiling {charter.tier_ceiling})\n"
        "\n"
        "[Timer]\n"
        f"{schedule}"
        f"Unit={unit}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return Binding(kind="systemd", charter_id=charter.id, cadence=described,
                   files={f"{unit}.service": service, f"{unit}.timer": timer})


# --------------------------------------------------------------------------
# windows-task
# --------------------------------------------------------------------------


def _windows_trigger(charter: Charter, context: HostContext) -> Tuple[str, str]:
    cadence = charter.cadence
    if cadence.kind == "manual":
        raise _refuse(charter, "the Windows scheduler",
                      "the cadence is manual",
                      "a manual charter has no schedule to render")
    if cadence.kind == "interval":
        n, unit = cadence.interval_parts()
        start = f"{context.start_date}T00:00:00"
        if unit == "d":
            return (
                "    <CalendarTrigger>\n"
                f"      <StartBoundary>{start}</StartBoundary>\n"
                "      <Enabled>true</Enabled>\n"
                "      <ScheduleByDay>\n"
                f"        <DaysInterval>{n}</DaysInterval>\n"
                "      </ScheduleByDay>\n"
                "    </CalendarTrigger>\n"), f"ScheduleByDay DaysInterval={n}"
        duration = f"PT{n}M" if unit == "m" else f"PT{n}H"
        return (
            "    <TimeTrigger>\n"
            f"      <StartBoundary>{start}</StartBoundary>\n"
            "      <Enabled>true</Enabled>\n"
            "      <Repetition>\n"
            f"        <Interval>{duration}</Interval>\n"
            "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
            "      </Repetition>\n"
            "    </TimeTrigger>\n"), f"Repetition {duration}"
    hh, mm = cadence.at_parts()
    start = f"{context.start_date}T{hh:02d}:{mm:02d}:00"
    if cadence.kind == "daily":
        return (
            "    <CalendarTrigger>\n"
            f"      <StartBoundary>{start}</StartBoundary>\n"
            "      <Enabled>true</Enabled>\n"
            "      <ScheduleByDay>\n"
            "        <DaysInterval>1</DaysInterval>\n"
            "      </ScheduleByDay>\n"
            "    </CalendarTrigger>\n"), "ScheduleByDay DaysInterval=1"
    day = _WEEKDAY_WINDOWS[str(cadence.weekday)]
    return (
        "    <CalendarTrigger>\n"
        f"      <StartBoundary>{start}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByWeek>\n"
        "        <DaysOfWeek>\n"
        f"          <{day} />\n"
        "        </DaysOfWeek>\n"
        "        <WeeksInterval>1</WeeksInterval>\n"
        "      </ScheduleByWeek>\n"
        "    </CalendarTrigger>\n"), f"ScheduleByWeek {day}"


def render_windows_task(charter: Charter, context: HostContext) -> Binding:
    """A Task Scheduler XML whose action is ``wscript`` plus a wrapper.

    Refuses when no wrapper is supplied, and refuses a wrapper that is itself a
    shell or interpreter -- both are the same defect, which is a console window
    on the operator's desktop at every fire.
    """
    wrapper = (context.windows_wrapper or "").strip()
    if not wrapper:
        raise AdapterError(
            "no windows_wrapper was supplied",
            "supply the windowless wrapper script the task should launch. The "
            "action is never a shell or interpreter executable: the console "
            "host is created before the interpreter parses its own hide flag, "
            "so a task that launches one directly flashes a window at every "
            "fire and no switch suppresses it", subject=charter.id)
    leaf = re.split(r"[\\/]", wrapper)[-1].lower()
    if leaf in _CONSOLE_ENGINES:
        raise AdapterError(
            f"windows_wrapper {leaf!r} is a shell or interpreter",
            "point windows_wrapper at a windowless wrapper script (the house "
            "pattern is a .vbs launched by wscript) which then starts the "
            "interpreter; the task action must not be the interpreter",
            subject=charter.id)

    trigger, described = _windows_trigger(charter, context)
    arguments = f'"{wrapper}" {context.command_for(charter)}'
    arguments_xml = _xml_escape(arguments, {'"': _QUOTE_ENTITY})
    uri = _xml_escape(charter.id)
    description = _xml_escape(
        f"{charter.purpose} (charter {charter.id}, ceiling "
        f"{charter.tier_ceiling}, kill switch {charter.kill_switch})")
    xml = (
        '<?xml version="1.0"?>\n'
        f'<Task version="1.4" xmlns="{_TASK_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <URI>\\IntentOps\\intentops-loop-{uri}</URI>\n"
        f"    <Description>{description}</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        f"{trigger}"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <Enabled>true</Enabled>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>wscript.exe</Command>\n"
        f"      <Arguments>{arguments_xml}</Arguments>\n"
        f"      <WorkingDirectory>{_xml_escape(context.node_root)}"
        "</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )
    return Binding(kind="windows-task", charter_id=charter.id,
                   cadence=described,
                   files={f"intentops-loop-{charter.id}.xml": xml})


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

_RENDERERS = {
    "cron": render_cron,
    "systemd": render_systemd,
    "windows-task": render_windows_task,
}


def render(kind: str, charter: Charter, context: HostContext) -> Binding:
    """Render one charter for one host kind. An undeclared kind is a HALT."""
    if kind not in ADAPTER_KINDS:
        raise AdapterError(f"adapter kind {kind!r} is undeclared",
                           "use one of: " + " | ".join(ADAPTER_KINDS),
                           subject=charter.id)
    return _RENDERERS[kind](charter, context)


def render_all(charter: Charter, context: HostContext
               ) -> Tuple[Dict[str, Binding], Dict[str, str]]:
    """Render every host kind. Returns (bindings, refusals-by-kind).

    A refusal is RETURNED, never swallowed: a host that cannot express a
    charter stays in the population carrying why, rather than quietly leaving
    it and making the coverage number look better.
    """
    bindings: Dict[str, Binding] = {}
    refusals: Dict[str, str] = {}
    for kind in ADAPTER_KINDS:
        try:
            bindings[kind] = render(kind, charter, context)
        except AdapterError as exc:
            refusals[kind] = exc.message
    return bindings, refusals


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

_CONTEXT = HostContext(node_root="/srv/node",
                       tick_command="intentops loops tick",
                       user="loops", windows_wrapper="scripts/launch-hidden.vbs")


def _charter(**over: object) -> Charter:
    from .charters import SCHEMA, parse_charters

    entry: Dict[str, object] = {
        "id": "loop-example",
        "purpose": "Compare two things and file each mismatch.",
        "cadence": {"kind": "interval", "every": "30m"},
        "tier_ceiling": "T1",
        "enabled": False,
        "budget_share": 0.1,
        "kill_switch": ".intentops/loops/example.disabled",
        "requires_evidence": ["the file and line of each mismatch"],
    }
    entry.update(over)
    return parse_charters({"schema": SCHEMA, "as_of": "2026-09-06",
                           "entries": [entry]})[0]


def selftest() -> Tuple[bool, str]:
    """Prove every adapter renders, and that each refusal actually fires."""
    failures: List[str] = []

    def check(label: str, cond: bool) -> None:
        if not cond:
            failures.append(label)

    plain = _charter()
    for kind in ADAPTER_KINDS:
        try:
            binding = render(kind, plain, _CONTEXT)
        except AdapterError as exc:
            failures.append(f"{kind} refused a plain 30m charter: {exc}")
            continue
        check(f"{kind} rendered no artifact", bool(binding.files))
        check(f"{kind} did not pin a working directory",
              any("/srv/node" in text for text in binding.files.values()))

    check("cron did not render the 30-minute step",
          "*/30 * * * *" in render_cron(plain, _CONTEXT).single_text())
    check("systemd rendered no timer",
          any(name.endswith(".timer")
              for name in render_systemd(plain, _CONTEXT).files))
    check("the Windows action is not the windowless launcher",
          "<Command>wscript.exe</Command>"
          in render_windows_task(plain, _CONTEXT).single_text())

    def refuses(label: str, fn) -> None:
        try:
            fn()
        except AdapterError:
            return
        failures.append(label)

    refuses("cron accepted a 7-minute interval it cannot express",
            lambda: render_cron(_charter(cadence={"kind": "interval",
                                                  "every": "7m"}), _CONTEXT))
    refuses("cron accepted a 5-hour interval it cannot express",
            lambda: render_cron(_charter(cadence={"kind": "interval",
                                                  "every": "5h"}), _CONTEXT))
    refuses("cron accepted a 3-day interval it has no field for",
            lambda: render_cron(_charter(cadence={"kind": "interval",
                                                  "every": "3d"}), _CONTEXT))
    refuses("an adapter rendered a manual cadence",
            lambda: render_systemd(_charter(cadence={"kind": "manual"}),
                                   _CONTEXT))
    refuses("the Windows adapter rendered with no wrapper",
            lambda: render_windows_task(
                plain, HostContext(node_root="/srv/node",
                                   tick_command="intentops loops tick")))
    refuses("the Windows adapter accepted an interpreter as the wrapper",
            lambda: render_windows_task(
                plain, HostContext(node_root="C:/node",
                                   tick_command="intentops loops tick",
                                   windows_wrapper="C:/w/pwsh.exe")))
    refuses("an undeclared adapter kind was accepted",
            lambda: render("launchd", plain, _CONTEXT))

    bindings, refusals = render_all(
        _charter(cadence={"kind": "interval", "every": "7m"}), _CONTEXT)
    check("render_all dropped the cron refusal instead of reporting it",
          "cron" in refusals and "cron" not in bindings)
    check("render_all lost the hosts that CAN express a 7m interval",
          {"systemd", "windows-task"} <= set(bindings))

    if failures:
        return False, "adapters: " + "; ".join(failures)
    return True, ("adapters: 3 kinds render; 7 named refusals fire; a refusal "
                  "stays in the population")
