"""Response contracts also exported for the JavaScript client and CI."""
from typing import Literal
from pydantic import BaseModel, ConfigDict


class Response(BaseModel):
    model_config=ConfigDict(extra='allow')


class UserIdentity(Response):
    id: str
    name: str
    role: Literal['admin','user']


class TaskSummary(Response):
    id: str
    accountId: str | None
    name: str
    time: str
    enabled: bool


class RunSummary(Response):
    id: str
    status: Literal['running','retrying','success','failed','skipped','submitted_unconfirmed']
    label: str


class AppBootstrap(Response):
    user: UserIdentity
    csrf: str
    accounts: list[dict]
    tasks: list[TaskSummary]
    runs: list[RunSummary]
    membership: dict
    resource_limits: dict
    activity: dict


class ProbeResponse(Response):
    id: str
    task_id: str
    run_id: str | None
    kind: Literal['selfcheck','reconcile']
    status: Literal['queued','running','succeeded','failed']
    result: dict | None
    status_url: str
    created_at: str


CONTRACTS={'app':AppBootstrap,'probe':ProbeResponse}
