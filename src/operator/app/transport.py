"""Composition conversion for CP text answers to typed reversible browser inputs."""

from collections.abc import Callable

from src.operator.contracts import Command, FieldSpec
from worker.transport import HttpTransport


class RealTransport(HttpTransport):
    """The CP now sends radio/checkbox answers as booleans . This only converts the exact
    text 'true'/'false' that an older CP or a hand-made command may still carry."""

    resolve_field: Callable[[Command], FieldSpec | None] = lambda self, command: None

    def poll(self, run_id: str) -> list[Command]:
        """Convert only exact true/false text for the checkpoint's named boolean control."""
        commands = super().poll(run_id)
        result = []
        for command in commands:
            field = self.resolve_field(command)
            if (
                command.action == "answer"
                and field
                and field.type in {"radio", "checkbox"}
                and isinstance(command.value, str)
                and command.value in {"true", "false"}
            ):
                command = command.model_copy(update={"value": command.value == "true"})
            result.append(command)
        return result
