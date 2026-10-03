"""Shared contracts v0.1a: import from src.operator.contracts."""

from .primitives import (
    ActionResult, Contract, FieldResult, FieldSpec, FillAction, FillReport,
    JobStatus, PageState, SubmissionResult,
)
from .ports import BrowserPort, ChannelPort, DataSourcePort, LedgerPort, LLMPort

from .state import (
    Answer, AnswerLibrary, ApprovalState, Command, DataSnapshot, Event, Goal,
    JobPosting, JobState, Profile, ReviewSnapshot, Rules, RunState, RunStatus, Upload, Usage,
)

CONTRACT_VERSION = "0.1"

__all__ = [
    "ActionResult", "Contract", "FieldResult", "FieldSpec", "FillAction", "FillReport",
    "JobStatus", "PageState", "SubmissionResult", "BrowserPort", "ChannelPort",
    "DataSourcePort", "LedgerPort", "LLMPort", "CONTRACT_VERSION",
    "Answer", "AnswerLibrary", "ApprovalState", "Command", "DataSnapshot", "Event", "Goal",
    "JobPosting", "JobState", "Profile", "ReviewSnapshot", "Rules", "RunState", "RunStatus",
    "Upload", "Usage",
]
