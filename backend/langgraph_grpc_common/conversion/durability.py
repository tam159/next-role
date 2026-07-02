from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph_grpc_common.proto import enum_durability_pb2

if TYPE_CHECKING:
    from langgraph.types import Durability


def durability_to_proto(
    durability: Durability,
) -> enum_durability_pb2.Durability.ValueType:
    match durability:
        case "async":
            return enum_durability_pb2.Durability.ASYNC
        case "sync":
            return enum_durability_pb2.Durability.SYNC
        case "exit":
            return enum_durability_pb2.Durability.EXIT
        case _:
            raise ValueError(f"invalid durability: {durability}")


def durability_from_proto(
    durability: enum_durability_pb2.Durability.ValueType,
) -> Durability | None:
    match durability:
        case enum_durability_pb2.Durability.ASYNC:
            return "async"
        case enum_durability_pb2.Durability.SYNC:
            return "sync"
        case enum_durability_pb2.Durability.EXIT:
            return "exit"
