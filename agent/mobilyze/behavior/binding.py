from __future__ import annotations

from enum import StrEnum
from typing import Literal

from agent.mobilyze.behavior.contract import BehaviorContract, ContractIdentity
from agent.mobilyze.behavior.core import BehaviorModel, Identifier, Revision


class ApprovalDecision(StrEnum):
    APPROVED = "approved"


class BindingState(StrEnum):
    BOUND = "bound"
    IMPLEMENTATION_STARTED = "implementation_started"


class ApprovalEvent(BehaviorModel):
    event_id: Identifier
    task_id: Identifier
    contract_identity: ContractIdentity
    target_revision: Revision
    approved_by: Identifier
    decision: Literal[ApprovalDecision.APPROVED] = ApprovalDecision.APPROVED


class TaskBinding(BehaviorModel):
    task_id: Identifier
    contract_identity: ContractIdentity
    target_revision: Revision
    approval_event_id: Identifier
    state: BindingState


def bind_task(
    *, task_id: str, contract: BehaviorContract, target_revision: str, approval: ApprovalEvent
) -> TaskBinding:
    _validate_approval(
        approval,
        task_id=task_id,
        identity=contract.identity,
        target_revision=target_revision,
    )
    return TaskBinding(
        task_id=task_id,
        contract_identity=contract.identity,
        target_revision=target_revision,
        approval_event_id=approval.event_id,
        state=BindingState.BOUND,
    )


def start_implementation(binding: TaskBinding, approval: ApprovalEvent) -> TaskBinding:
    _validate_approval(
        approval,
        task_id=binding.task_id,
        identity=binding.contract_identity,
        target_revision=binding.target_revision,
    )
    return binding.model_copy(
        update={
            "approval_event_id": approval.event_id,
            "state": BindingState.IMPLEMENTATION_STARTED,
        }
    )


def transition_contract(
    binding: TaskBinding,
    contract: BehaviorContract,
    target_revision: str,
    approval: ApprovalEvent,
) -> TaskBinding:
    _validate_approval(
        approval,
        task_id=binding.task_id,
        identity=contract.identity,
        target_revision=target_revision,
    )
    if contract.contract_id != binding.contract_identity.contract_id:
        raise ValueError("a task binding cannot transition to a different contract ID")
    content_changed = contract.content_hash != binding.contract_identity.content_hash
    version_changed = contract.version != binding.contract_identity.version
    if content_changed and approval.event_id == binding.approval_event_id:
        raise ValueError("changed contract content requires a new approval event")
    if not content_changed and version_changed:
        raise ValueError("unchanged contract content must retain its version")
    if content_changed and binding.state is BindingState.IMPLEMENTATION_STARTED:
        if contract.version != binding.contract_identity.version + 1:
            raise ValueError("changed contract content requires the next version after start")
    if content_changed and binding.state is BindingState.BOUND:
        allowed_versions = {
            binding.contract_identity.version,
            binding.contract_identity.version + 1,
        }
        if contract.version not in allowed_versions:
            raise ValueError("pre-start contract changes must retain or increment the version")
    return TaskBinding(
        task_id=binding.task_id,
        contract_identity=contract.identity,
        target_revision=target_revision,
        approval_event_id=approval.event_id,
        state=binding.state,
    )


def _validate_approval(
    approval: ApprovalEvent,
    *,
    task_id: str,
    identity: ContractIdentity,
    target_revision: str,
) -> None:
    if approval.task_id != task_id:
        raise ValueError("approval event is bound to a different task")
    if approval.contract_identity != identity:
        raise ValueError("approval event is bound to a different contract identity")
    if approval.target_revision != target_revision:
        raise ValueError("approval event is bound to a different target revision")
