"""Normalize and compare the effective Scheduler task contract."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree


class SchedulerContractError(ValueError):
    """A task definition is malformed or differs from the expected contract."""

    def __init__(self, code: str, fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SchedulerTaskContract:
    action: str
    arguments: str
    working_directory: str
    user_id: str
    logon_type: str
    run_level: str
    execution_time_limit: str
    multiple_instances: str
    hidden: bool


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _one_text(root: ElementTree.Element, path: tuple[str, ...]) -> str:
    current = [root]
    for name in path:
        current = [
            child
            for parent in current
            for child in parent
            if _local_name(child.tag) == name
        ]
    if len(current) != 1 or current[0].text is None:
        raise SchedulerContractError("scheduler_definition_invalid")
    return current[0].text.strip()


def _one_element(root: ElementTree.Element, name: str) -> ElementTree.Element:
    matches = [item for item in root.iter() if _local_name(item.tag) == name]
    if len(matches) != 1:
        raise SchedulerContractError("scheduler_definition_invalid")
    return matches[0]


def parse_task_definition(xml_text: str) -> SchedulerTaskContract:
    """Read the action and the settings which affect execution and overlap."""

    if not isinstance(xml_text, str) or not xml_text.strip():
        raise SchedulerContractError("scheduler_definition_invalid")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SchedulerContractError("scheduler_definition_invalid") from exc
    actions = [item for item in root.iter() if _local_name(item.tag) == "Exec"]
    principals = [item for item in root.iter() if _local_name(item.tag) == "Principal"]
    settings = _one_element(root, "Settings")
    if len(actions) != 1 or len(principals) != 1:
        raise SchedulerContractError("scheduler_definition_invalid")
    action = actions[0]
    principal = principals[0]
    try:
        execute = _one_text(action, ("Command",))
        arguments = _one_text(action, ("Arguments",))
        working_directory = _one_text(action, ("WorkingDirectory",))
        user_id = _one_text(principal, ("UserId",))
        logon_type = _one_text(principal, ("LogonType",))
        run_level = _one_text(principal, ("RunLevel",))
        execution_time_limit = _one_text(settings, ("ExecutionTimeLimit",))
        multiple_instances = _one_text(settings, ("MultipleInstancesPolicy",))
        hidden_text = _one_text(settings, ("Hidden",)).casefold()
    except SchedulerContractError:
        raise
    if hidden_text not in {"true", "false"}:
        raise SchedulerContractError("scheduler_definition_invalid")
    return SchedulerTaskContract(
        action=execute,
        arguments=arguments,
        working_directory=working_directory,
        user_id=user_id,
        logon_type=logon_type,
        run_level=run_level,
        execution_time_limit=execution_time_limit,
        multiple_instances=multiple_instances,
        hidden=hidden_text == "true",
    )


def compare_task_definitions(
    expected_xml: str, observed_xml: str
) -> SchedulerTaskContract:
    """Fail closed when the exported registered task drifts from its template."""

    expected = parse_task_definition(expected_xml)
    observed = parse_task_definition(observed_xml)
    differences = tuple(
        field
        for field in SchedulerTaskContract.__dataclass_fields__
        if getattr(expected, field) != getattr(observed, field)
    )
    if differences:
        raise SchedulerContractError("scheduler_definition_drift", differences)
    return observed
