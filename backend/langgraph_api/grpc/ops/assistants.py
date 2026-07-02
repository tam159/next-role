"""gRPC-based assistants operations."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langgraph_grpc_common.conversion import config as config_conversion
from langgraph_grpc_common.proto import core_api_pb2 as pb

from langgraph_api.grpc.client import get_shared_client
from langgraph_api.grpc.ops import (
    Authenticated,
    _map_sort_order,
    build_encryption_context,
    consolidate_config_and_context,
    grpc_error_guard,
    map_if_exists,
)
from langgraph_api.schema import (
    Assistant,
    AssistantSelectField,
    Config,
    Context,
    MetadataInput,
    OnConflictBehavior,
)
from langgraph_api.serde import json_dumpb_optional, json_loads_optional

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def proto_to_assistant(proto_assistant: pb.Assistant) -> Assistant:
    """Convert protobuf Assistant to dictionary format."""
    # Preserve None for optional scalar fields by checking presence via HasField
    description = (
        proto_assistant.description if proto_assistant.HasField("description") else None
    )
    return {
        "assistant_id": proto_assistant.assistant_id,
        "graph_id": proto_assistant.graph_id,
        "version": proto_assistant.version,
        "created_at": proto_assistant.created_at.ToDatetime(tzinfo=UTC),
        "updated_at": proto_assistant.updated_at.ToDatetime(tzinfo=UTC),
        "config": config_conversion.config_from_proto(proto_assistant.config),
        "context": json_loads_optional(proto_assistant.context_json),
        "metadata": json_loads_optional(proto_assistant.metadata_json),
        "name": proto_assistant.name,
        "description": description,
    }


def _map_sort_by(sort_by: str | None) -> pb.AssistantsSortBy:
    """Map string sort_by to protobuf enum."""
    if not sort_by:
        return pb.AssistantsSortBy.CREATED_AT

    sort_by_lower = sort_by.lower()
    mapping = {
        "assistant_id": pb.AssistantsSortBy.ASSISTANT_ID,
        "graph_id": pb.AssistantsSortBy.GRAPH_ID,
        "name": pb.AssistantsSortBy.NAME,
        "created_at": pb.AssistantsSortBy.CREATED_AT,
        "updated_at": pb.AssistantsSortBy.UPDATED_AT,
    }
    return mapping.get(sort_by_lower, pb.AssistantsSortBy.CREATED_AT)


@grpc_error_guard
class Assistants(Authenticated):
    """gRPC-based assistants operations."""

    resource = "assistants"

    @staticmethod
    async def search(
        conn,  # Not used in gRPC implementation
        *,
        graph_id: str | None,
        name: str | None,
        metadata: MetadataInput,
        limit: int,
        offset: int,
        sort_by: str | None = None,
        sort_order: str | None = None,
        select: list[AssistantSelectField] | None = None,
        ctx: Any = None,
    ) -> tuple[AsyncIterator[Assistant], int | None]:
        """Search assistants via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx,
            "search",
            {
                "graph_id": graph_id,
                "metadata": metadata,
                "limit": limit,
                "offset": offset,
            },
        )

        # Build the gRPC request
        request = pb.SearchAssistantsRequest(
            filters=auth_filters,
            graph_id=graph_id,
            metadata_json=json_dumpb_optional(metadata),
            limit=limit,
            offset=offset,
            sort_by=_map_sort_by(sort_by),
            sort_order=_map_sort_order(sort_order),
            select=select,
            name=name,
        )

        client = await get_shared_client()
        response = await client.assistants.Search(request)

        # Convert response to expected format
        assistants = [
            proto_to_assistant(assistant) for assistant in response.assistants
        ]

        # Determine if there are more results
        # Note: gRPC doesn't return cursor info, so we estimate based on result count
        cursor = offset + limit if len(assistants) == limit else None

        async def generate_results():
            for assistant in assistants:
                yield {
                    k: v for k, v in assistant.items() if select is None or k in select
                }

        return generate_results(), cursor

    @staticmethod
    async def get(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        ctx: Any = None,
    ) -> AsyncIterator[Assistant]:
        """Get assistant by ID via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx, "read", {"assistant_id": str(assistant_id)}
        )

        # Build the gRPC request
        request = pb.GetAssistantRequest(
            assistant_id=str(assistant_id),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.assistants.Get(request)

        # Convert and yield the result
        assistant = proto_to_assistant(response)

        async def generate_result():
            yield assistant

        return generate_result()

    @staticmethod
    async def put(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        *,
        graph_id: str,
        config: Config,
        context: Context,
        metadata: MetadataInput,
        if_exists: OnConflictBehavior,
        name: str,
        description: str | None = None,
        ctx: Any = None,
        system: bool = False,
    ) -> AsyncIterator[Assistant]:
        """Create/update assistant via gRPC."""
        metadata = metadata if metadata is not None else {}
        config = config if config is not None else Config()
        context = context or {}
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx,
            "create",
            {
                "assistant_id": str(assistant_id),
                "graph_id": graph_id,
                "config": config,
                "context": context,
                "metadata": metadata,
                "name": name,
                "description": description,
            },
        )

        config, context = consolidate_config_and_context(config, context)

        on_conflict = map_if_exists(if_exists)

        # Don't encrypt system-owned assistants (generally created on startup).
        encryption_context = None if system else build_encryption_context("assistant")

        request = pb.CreateAssistantRequest(
            assistant_id=str(assistant_id),
            graph_id=graph_id,
            filters=auth_filters,
            if_exists=on_conflict,
            config=config_conversion.config_to_proto(config),
            context_json=json_dumpb_optional(context),
            metadata_json=json_dumpb_optional(metadata),
            name=name,
            description=description,
            encryption_context=encryption_context,
        )

        client = await get_shared_client()
        response = await client.assistants.Create(request)

        # Convert and yield the result
        assistant = proto_to_assistant(response)

        async def generate_result():
            yield assistant

        return generate_result()

    @staticmethod
    async def patch(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        *,
        config: Config | None = None,
        context: Context | None = None,
        graph_id: str | None = None,
        metadata: MetadataInput | None = None,
        name: str | None = None,
        description: str | None = None,
        ctx: Any = None,
    ) -> AsyncIterator[Assistant]:
        """Update assistant via gRPC."""
        metadata = metadata if metadata is not None else {}
        config = config if config is not None else Config()
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx,
            "update",
            {
                "assistant_id": str(assistant_id),
                "graph_id": graph_id,
                "config": config,
                "context": context,
                "metadata": metadata,
                "name": name,
                "description": description,
            },
        )

        config, context = consolidate_config_and_context(config, context)

        # Build the gRPC request
        request = pb.PatchAssistantRequest(
            assistant_id=str(assistant_id),
            filters=auth_filters,
            graph_id=graph_id,
            context_json=json_dumpb_optional(context),
            metadata_json=json_dumpb_optional(metadata),
            name=name,
            description=description,
            encryption_context=build_encryption_context("assistant"),
        )

        # Add optional config if provided
        if config:
            request.config.CopyFrom(config_conversion.config_to_proto(config))

        client = await get_shared_client()
        response = await client.assistants.Patch(request)

        # Convert and yield the result
        assistant = proto_to_assistant(response)

        async def generate_result():
            yield assistant

        return generate_result()

    @staticmethod
    async def delete(
        conn: Any,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        ctx: Any = None,
        *,
        delete_threads: bool = False,
    ) -> AsyncIterator[UUID]:
        """Delete assistant via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx, "delete", {"assistant_id": str(assistant_id)}
        )

        # Build the gRPC request
        request = pb.DeleteAssistantRequest(
            assistant_id=str(assistant_id),
            filters=auth_filters,
            delete_threads=delete_threads,
        )

        client = await get_shared_client()
        await client.assistants.Delete(request)

        # Return the deleted ID
        async def generate_result():
            yield UUID(str(assistant_id))

        return generate_result()

    @staticmethod
    async def set_latest(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        version: int,
        ctx: Any = None,
    ) -> AsyncIterator[Assistant]:
        """Set latest version of assistant via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx,
            "update",
            {
                "assistant_id": str(assistant_id),
                "version": version,
            },
        )

        # Build the gRPC request
        request = pb.SetLatestAssistantRequest(
            assistant_id=str(assistant_id),
            version=version,
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.assistants.SetLatest(request)

        # Convert and yield the result
        assistant = proto_to_assistant(response)

        async def generate_result():
            yield assistant

        return generate_result()

    @staticmethod
    async def get_versions(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID | str,
        metadata: MetadataInput,
        limit: int,
        offset: int,
        ctx: Any = None,
    ) -> AsyncIterator[Assistant]:
        """Get all versions of assistant via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx,
            "search",
            {"assistant_id": str(assistant_id), "metadata": metadata},
        )

        # Build the gRPC request
        request = pb.GetAssistantVersionsRequest(
            assistant_id=str(assistant_id),
            filters=auth_filters,
            metadata_json=json_dumpb_optional(metadata),
            limit=limit,
            offset=offset,
        )

        client = await get_shared_client()
        response = await client.assistants.GetVersions(request)

        # Convert and yield the results
        async def generate_results():
            for version in response.versions:
                # Preserve None for optional scalar fields by checking presence
                version_description = (
                    version.description if version.HasField("description") else None
                )
                yield {
                    "assistant_id": version.assistant_id,
                    "graph_id": version.graph_id,
                    "version": version.version,
                    "created_at": version.created_at.ToDatetime(tzinfo=UTC),
                    "config": config_conversion.config_from_proto(version.config),
                    "context": json_loads_optional(version.context_json),
                    "metadata": json_loads_optional(version.metadata_json),
                    "name": version.name,
                    "description": version_description,
                }

        return generate_results()

    @staticmethod
    async def count(
        conn,  # Not used in gRPC implementation
        *,
        graph_id: str | None = None,
        name: str | None = None,
        metadata: MetadataInput = None,
        ctx: Any = None,
    ) -> int:
        """Count assistants via gRPC."""
        # Handle auth filters
        auth_filters = await Assistants.handle_event(
            ctx, "search", {"graph_id": graph_id, "metadata": metadata}
        )

        # Build the gRPC request
        request = pb.CountAssistantsRequest(
            filters=auth_filters,
            graph_id=graph_id,
            name=name,
            metadata_json=json_dumpb_optional(metadata),
        )

        client = await get_shared_client()
        response = await client.assistants.Count(request)

        return int(response.count)
