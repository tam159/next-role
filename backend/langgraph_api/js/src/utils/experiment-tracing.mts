// Helpers for routing studio-experiment runs to a separate LangSmith project
// when a JS graph is invoked via the Node sidecar.
//
// The Python worker (api/langgraph_api/stream.py) reads the run-creation
// payload's `langsmith_tracer` field and stores it under the reserved
// configurable keys `__langsmith_project__` / `__langsmith_example_id__`
// (see api/langgraph_api/models/run.py). For Python graphs it then wraps
// execution in `langsmith.tracing_context(replicas=[...])`. Python contextvars
// don't cross the HTTP boundary into the JS sidecar, so we read the same
// reserved keys here and produce an equivalent replica list that the JS
// sidecar can pass to a `LangChainTracer`.

export interface ExperimentReplica {
  projectName: string;
  updates?: { reference_example_id: string };
}

/**
 * Build the LangSmith tracing replicas for a JS streamEvents call.
 *
 * Returns `undefined` when the run was not dispatched as part of a studio
 * experiment (i.e. `__langsmith_project__` is not set). When set, returns
 * two replicas mirroring the Python worker:
 *   1. the experiment project (carrying `reference_example_id` when an
 *      example_id is present) — links runs to dataset rows in the experiment
 *      view.
 *   2. the deployment's env-default project (`LANGSMITH_PROJECT` /
 *      `LANGCHAIN_PROJECT`) — keeps the deployment-level trace stream
 *      unchanged for non-experiment observability.
 */
export function buildExperimentReplicas(
  configurable: Record<string, unknown> | undefined,
  env: NodeJS.ProcessEnv = process.env,
): ExperimentReplica[] | undefined {
  const lsProject = configurable?.["__langsmith_project__"];
  if (typeof lsProject !== "string" || lsProject.length === 0) return undefined;

  const lsExampleId = configurable?.["__langsmith_example_id__"];
  const envProject =
    env.LANGSMITH_PROJECT ?? env.LANGCHAIN_PROJECT ?? "default";

  return [
    {
      projectName: lsProject,
      ...(typeof lsExampleId === "string" && lsExampleId.length > 0
        ? { updates: { reference_example_id: lsExampleId } }
        : {}),
    },
    { projectName: envProject },
  ];
}
