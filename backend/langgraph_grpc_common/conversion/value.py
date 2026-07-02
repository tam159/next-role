from typing import Any, cast

import orjson
from langgraph.types import Command, Send

from langgraph_grpc_common import serde
from langgraph_grpc_common.conversion._compat import MISSING, TASKS
from langgraph_grpc_common.proto import engine_common_pb2


def serialized_value_from_proto(value: engine_common_pb2.SerializedValue) -> Any:
    if value.encoding == "json":
        # For JSON payload from core server, we deserialize as a raw JSON format.
        # This will avoid using deserializer from serde which will recursively revive the JSON blobs from legacy JSON serializer
        return orjson.loads(value.value)
    deserializer = serde.get_serializer()
    return deserializer.loads_typed((value.encoding, value.value))


def any_to_serialized_value(value: Any) -> engine_common_pb2.SerializedValue:
    if isinstance(value, tuple):
        value = [value]
    encoding, ser_val = serde.get_serializer().dumps_typed(value)
    return engine_common_pb2.SerializedValue(encoding=encoding, value=bytes(ser_val))


def value_from_proto(value: engine_common_pb2.ChannelValue) -> Any:
    """Convert a ChannelValue proto to a Python value.

    Note:
    Task outputs with Command are expanded by LangGraph before reaching the executor.
    """
    value_kind = value.WhichOneof("val")
    if value_kind is None:
        return None
    if value_kind == "serialized_value":
        return serialized_value_from_proto(value.serialized_value)
    if value_kind == "sends":
        sends = []
        for send in value.sends.sends:
            node = send.node
            arg = serialized_value_from_proto(send.arg)
            sends.append(Send(node, arg))
        return sends
    if value_kind == "missing":
        return MISSING
    raise NotImplementedError(f"Unrecognized value kind: {value_kind}")


def value_to_proto(
    channel_name: str | None, value: Any
) -> engine_common_pb2.ChannelValue:
    """Convert a Python value to a ChannelValue proto."""
    if channel_name == TASKS and value != MISSING:
        if not isinstance(value, list):
            if not isinstance(value, Send):
                raise ValueError(
                    "Task must be a Send object objects."
                    f" Got type={type(value)} value={value}",
                )
            value = [value]
        else:
            for v in value:
                if not isinstance(v, Send):
                    raise ValueError(
                        "Task must be a list of Send objects."
                        f" Got types={[type(v) for v in value]} values={value}",
                    )
        return sends_to_proto(value)
    if value == MISSING:
        return missing_to_proto()
    if isinstance(value, Command):
        raise ValueError(
            "Command should not appear in task outputs. "
            "Commands are expanded by LangGraph before reaching the executor. "
            "Use input_to_proto() for RunInput with Command."
        )
    return base_value_to_proto(value)


def send_to_proto(send: Send) -> engine_common_pb2.Send:
    encoding, ser_val = serde.get_serializer().dumps_typed(send.arg)
    return engine_common_pb2.Send(
        node=send.node,
        arg=engine_common_pb2.SerializedValue(encoding=encoding, value=ser_val),
    )


def sends_to_proto(sends: list[Send]) -> engine_common_pb2.ChannelValue:
    if not sends:
        return missing_to_proto()
    pb = []
    for send in sends:
        pb.append(send_to_proto(send))

    return engine_common_pb2.ChannelValue(sends=engine_common_pb2.Sends(sends=pb))


def command_to_proto(cmd: Command) -> engine_common_pb2.Command:
    """Convert a Command object to a Command proto."""
    cmd_pb = engine_common_pb2.Command()
    if cmd.graph:
        cmd_pb.graph = cmd.graph
    if cmd.update:
        if isinstance(cmd.update, dict):
            for k, v in cmd.update.items():
                encoding, ser_val = serde.get_serializer().dumps_typed(v)
                cmd_pb.update[k].CopyFrom(
                    engine_common_pb2.SerializedValue(
                        encoding=encoding, value=bytes(ser_val)
                    )
                )
        else:
            encoding, ser_val = serde.get_serializer().dumps_typed(cmd.update)
            cmd_pb.update["__root__"].CopyFrom(
                engine_common_pb2.SerializedValue(
                    encoding=encoding, value=bytes(ser_val)
                )
            )
    if cmd.resume:
        if isinstance(cmd.resume, dict):
            cmd_pb.resume.CopyFrom(resume_map_to_proto(cmd.resume))
        else:
            resume_val = engine_common_pb2.Resume(
                value=any_to_serialized_value(cmd.resume)
            )
            cmd_pb.resume.CopyFrom(resume_val)
    if cmd.goto:
        gotos = []
        goto = cmd.goto
        if isinstance(goto, list):
            for g in goto:
                gotos.append(goto_to_proto(g))
        else:
            gotos.append(goto_to_proto(cast("Send | str", goto)))
        cmd_pb.gotos.extend(gotos)

    return cmd_pb


def resume_map_to_proto(resume: dict[str, Any] | Any) -> engine_common_pb2.Resume:
    vals = {k: any_to_serialized_value(v) for k, v in resume.items()}
    return engine_common_pb2.Resume(
        values=engine_common_pb2.InterruptValues(values=vals)
    )


def command_from_proto(cmd: engine_common_pb2.Command) -> Command:
    """Constructs a Command object from a Command proto."""
    graph = cmd.graph if cmd.graph else None

    update = None
    if cmd.update:
        if "__root__" in cmd.update:
            update = serialized_value_from_proto(cmd.update["__root__"])
        else:
            update = {k: serialized_value_from_proto(v) for k, v in cmd.update.items()}

    resume = None
    if cmd.HasField("resume"):
        resume_which = cmd.resume.WhichOneof("message")
        if resume_which == "value":
            resume = serialized_value_from_proto(cmd.resume.value)
        elif resume_which == "values":
            resume = {
                k: serialized_value_from_proto(v)
                for k, v in cmd.resume.values.values.items()
            }

    goto = ()
    if cmd.gotos:
        gotos = []
        for goto_pb in cmd.gotos:
            goto_which = goto_pb.WhichOneof("message")
            if goto_which == "node_name":
                gotos.append(goto_pb.node_name)
            elif goto_which == "send":
                gotos.append(
                    Send(
                        goto_pb.send.node,
                        serialized_value_from_proto(goto_pb.send.arg),
                    )
                )
        goto = gotos[0] if len(gotos) == 1 else gotos

    return Command(graph=graph, update=update, resume=resume, goto=goto)


def goto_to_proto(goto: Send | str) -> engine_common_pb2.Goto:
    if isinstance(goto, Send):
        return engine_common_pb2.Goto(send=send_to_proto(goto))
    if isinstance(goto, str):
        return engine_common_pb2.Goto(node_name=goto)
    raise ValueError("goto must be send or node name")


def missing_to_proto() -> engine_common_pb2.ChannelValue:
    from google.protobuf import empty_pb2  # noqa: PLC0415

    return engine_common_pb2.ChannelValue(missing=empty_pb2.Empty())


def base_value_to_proto(value: Any) -> engine_common_pb2.ChannelValue:
    encoding, ser_val = serde.get_serializer().dumps_typed(value)
    serialize_value_proto = engine_common_pb2.SerializedValue(
        encoding=encoding, value=bytes(ser_val)
    )

    return engine_common_pb2.ChannelValue(serialized_value=serialize_value_proto)
