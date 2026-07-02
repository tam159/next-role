from __future__ import annotations

import typing

from langgraph.types import Interrupt, StateSnapshot

from langgraph_api.feature_flags import USE_NEW_INTERRUPTS
from langgraph_api.js.base import RemoteInterrupt

if typing.TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

    from langgraph_api.schema import Checkpoint, DeprecatedInterrupt, ThreadState
    from langgraph_api.schema import Interrupt as InterruptSchema


def runnable_config_to_checkpoint(
    config: RunnableConfig | None,
) -> Checkpoint | None:
    if (
        not config
        or not config["configurable"]
        or "thread_id" not in config["configurable"]
        or not config["configurable"]["thread_id"]
        or "checkpoint_id" not in config["configurable"]
        or not config["configurable"]["checkpoint_id"]
    ):
        return None

    configurable = config["configurable"]
    checkpoint: Checkpoint = {
        "checkpoint_id": configurable["checkpoint_id"],
        "thread_id": configurable["thread_id"],
    }

    if "checkpoint_ns" in configurable:
        checkpoint["checkpoint_ns"] = configurable["checkpoint_ns"] or ""

    if "checkpoint_map" in configurable:
        checkpoint["checkpoint_map"] = configurable["checkpoint_map"]

    return checkpoint


def patch_interrupt(
    interrupt: Interrupt | RemoteInterrupt | dict,
) -> InterruptSchema | DeprecatedInterrupt:
    """Convert a langgraph interrupt (v0 or v1) to standard interrupt schema.

    In v0.4 and v0.5, interrupt_id is a property on the langgraph.types.Interrupt object,
    so we reconstruct the type in order to access the id, with compatibility for the new
    v0.6 interrupt format as well.
    """

    # This is coming from JS, which already contains the interrupt ID.
    # Stay on the safe side and pass-through the interrupt ID if it exists.
    if isinstance(interrupt, RemoteInterrupt):
        id = interrupt.raw.pop("interrupt_id", None) or interrupt.raw.pop("id", None)
        if id is None:
            return interrupt.raw
        return {"id": id, **interrupt.raw}

    if USE_NEW_INTERRUPTS:
        interrupt = Interrupt(**interrupt) if isinstance(interrupt, dict) else interrupt

        return {
            "id": interrupt.id,
            "value": interrupt.value,
        }
    else:
        if isinstance(interrupt, dict):
            # interrupt_id is a deprecated property on Interrupt and should not be used for initialization
            # id is the new field we use for identification, also not supported on init for old versions
            interrupt.pop("interrupt_id", None)
            interrupt.pop("id", None)
            interrupt = Interrupt(**interrupt)

        return {
            "id": interrupt.interrupt_id
            if hasattr(interrupt, "interrupt_id")
            else None,
            "value": interrupt.value,
            "resumable": interrupt.resumable,
            "ns": interrupt.ns,
            "when": interrupt.when,
        }


def state_snapshot_to_thread_state(state: StateSnapshot) -> ThreadState:
    return {
        "values": state.values,
        "next": state.next,
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "path": t.path,
                "error": t.error,
                "interrupts": [patch_interrupt(i) for i in t.interrupts],
                "checkpoint": t.state["configurable"]
                if t.state is not None and not isinstance(t.state, StateSnapshot)
                else None,
                "state": state_snapshot_to_thread_state(t.state)
                if isinstance(t.state, StateSnapshot)
                else None,
                "result": getattr(t, "result", None),
            }
            for t in state.tasks
        ],
        "metadata": state.metadata,
        "created_at": state.created_at,
        "checkpoint": runnable_config_to_checkpoint(state.config),
        "parent_checkpoint": runnable_config_to_checkpoint(state.parent_config),
        "interrupts": [patch_interrupt(i) for i in getattr(state, "interrupts", [])],
        # below are deprecated
        "checkpoint_id": state.config["configurable"].get("checkpoint_id")
        if state.config
        else None,
        "parent_checkpoint_id": state.parent_config["configurable"]["checkpoint_id"]
        if state.parent_config
        else None,
    }
