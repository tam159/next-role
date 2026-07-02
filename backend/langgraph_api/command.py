from typing import cast

from langgraph.types import Command, Send

from langgraph_api.schema import RunCommand


def map_cmd(cmd: RunCommand) -> Command:
    goto = cmd.get("goto")
    if goto is not None and not isinstance(goto, list):
        goto = [cmd.get("goto")]

    update = cmd.get("update")
    if isinstance(update, tuple | list) and all(
        isinstance(t, tuple | list) and len(t) == 2 and isinstance(t[0], str)
        for t in cast("list", update)
    ):
        update = [tuple(t) for t in cast("list", update)]

    return Command(
        update=update,
        goto=(
            [
                it if isinstance(it, str) else Send(it["node"], it["input"])
                for it in goto
            ]
            if goto
            else None
        ),
        resume=cmd.get("resume"),
    )
