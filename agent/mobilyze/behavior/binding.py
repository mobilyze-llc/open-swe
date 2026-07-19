from __future__ import annotations

from dataclasses import dataclass, replace

from agent.mobilyze.behavior.codec import canonical_hash
from agent.mobilyze.behavior.models import BehaviorContract, ContractOwner
from agent.mobilyze.behavior.policy import require_text


class ContractMutationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    event_id: str
    approved_by: str
    owner: ContractOwner
    contract_version: int
    contract_hash: str

    def __post_init__(self) -> None:
        try:
            require_text(self.event_id, "approval event id")
            require_text(self.approved_by, "approver reference")
        except ValueError as error:
            raise ContractMutationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class AcceptedContract:
    contract: BehaviorContract
    contract_hash: str
    approval_events: tuple[ApprovalEvent, ...]
    implementation_started_event: str | None = None

    def __post_init__(self) -> None:
        if canonical_hash(self.contract) != self.contract_hash:
            raise ContractMutationError(
                "accepted contract content does not match its persisted hash"
            )
        if not self.approval_events:
            raise ContractMutationError("accepted contract requires an approval event")
        latest = self.approval_events[-1]
        _validate_approval(self.contract, latest)


def _validate_approval(contract: BehaviorContract, approval: ApprovalEvent) -> None:
    if approval.owner != contract.owner:
        raise ContractMutationError("approval owner does not match the contract owner")
    if approval.contract_version != contract.contract_version:
        raise ContractMutationError("approval version does not match the contract version")
    if approval.contract_hash != canonical_hash(contract):
        raise ContractMutationError("approval hash does not match the contract content")


def accept_contract(contract: BehaviorContract, approval: ApprovalEvent) -> AcceptedContract:
    _validate_approval(contract, approval)
    return AcceptedContract(
        contract=contract,
        contract_hash=canonical_hash(contract),
        approval_events=(approval,),
    )


def start_implementation(binding: AcceptedContract, *, event_id: str) -> AcceptedContract:
    if binding.implementation_started_event is not None:
        raise ContractMutationError("implementation start is already recorded")
    try:
        require_text(event_id, "implementation start event id")
    except ValueError as error:
        raise ContractMutationError(str(error)) from error
    return replace(binding, implementation_started_event=event_id)


def amend_contract(
    binding: AcceptedContract,
    contract: BehaviorContract,
    approval: ApprovalEvent | None,
) -> AcceptedContract:
    if contract.owner != binding.contract.owner:
        raise ContractMutationError("contract owner cannot change")
    content_changed = canonical_hash(contract) != binding.contract_hash
    if not content_changed:
        return binding
    if (
        binding.implementation_started_event is not None
        and contract.contract_version <= binding.contract.contract_version
    ):
        raise ContractMutationError(
            "mutation after implementation start must increase contract_version"
        )
    if approval is None:
        raise ContractMutationError("contract mutation requires an explicit approval event")
    if any(event.event_id == approval.event_id for event in binding.approval_events):
        raise ContractMutationError("contract mutation requires a new approval event")
    _validate_approval(contract, approval)
    return AcceptedContract(
        contract=contract,
        contract_hash=canonical_hash(contract),
        approval_events=(*binding.approval_events, approval),
        implementation_started_event=binding.implementation_started_event,
    )
