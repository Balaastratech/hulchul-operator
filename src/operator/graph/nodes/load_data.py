"""Validate and pin candidate data at run start."""

from ..runtime import GraphState, Services
from src.operator.contracts import DataSnapshot
from ..runtime import read_run, update


def load_data(state: GraphState, services: Services) -> dict:
    """Load once; state holds the same snapshot across resumes."""
    run = read_run(state)
    data = DataSnapshot.model_validate(services.call(services.data.load(run.run_id)))
    run.data_snapshot_hash = data.snapshot_hash
    services.emit("E01", run, "Run started with validated synthetic data")
    return update(run, data=data.model_dump(mode="json"))
