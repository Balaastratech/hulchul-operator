"""Validate and pin candidate data at run start."""

from src.operator.contracts import DataSnapshot

from ..runtime import GraphState, Services, read_run, update


def load_data(state: GraphState, services: Services) -> dict:
    """Load once; state holds the same snapshot across resumes."""
    run = read_run(state)
    loaded = services.call(services.data.load(run.run_id))
    data = DataSnapshot.model_validate(loaded.model_dump(mode="json"))
    run.data_snapshot_hash = data.snapshot_hash
    services.emit("E01", run, "Run started with validated synthetic data")
    return update(run, data=data.model_dump(mode="json"))
