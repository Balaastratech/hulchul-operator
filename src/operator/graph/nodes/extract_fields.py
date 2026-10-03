"""Extract and reject ambiguous stable field identities."""

from ..runtime import GraphState, Services, active_job, read_run, update


def extract_fields(state: GraphState, services: Services) -> dict:
    """Read DOM via BrowserPort; no page text is executed as instructions."""
    run = read_run(state)
    job = active_job(run)
    current = services.call(services.browser.extract_fields())
    keys = [field.key for field in current]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate extracted stable keys")
    accumulated = {field.key: field for field in job.fields}
    for field in current:
        old = accumulated.get(field.key)
        if old is not None and old.id != field.id:
            raise ValueError("stable field key changed its control identity")
        accumulated[field.key] = field
    job.fields = list(accumulated.values())
    return update(run, current_keys=keys)
