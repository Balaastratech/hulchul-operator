"""Extract and reject ambiguous stable field identities."""

from ..runtime import GraphState, Services
from ..runtime import active_job, read_run, update


def extract_fields(state: GraphState, services: Services) -> dict:
    """Read DOM via BrowserPort; no page text is executed as instructions."""
    run = read_run(state)
    job = active_job(run)
    job.fields = services.call(services.browser.extract_fields())
    keys = [field.key for field in job.fields]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate extracted stable keys")
    return update(run)
