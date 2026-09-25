# POC Builder API Agent

Generates the executable OpenAPI contract and Node/Express backend, including bounded backend
repairs. Its HTTP tool accepts a direct `AgentEnvelope`; only `request.poc_id` is mandatory.
The agent retrieves the POC document from the configured MongoDB database and collection, then
loads `requirements`, `api_contract`, `query_patterns`, and `data_model` from the Git URLs in
`spec_artifacts` before generation.

## Runtime

The graph is defined in `src/main.py`. The deployment exposes `invoke_api_agent`; Coding
Orchestrator calls its Magenta HTTP tool endpoint through `POC_API_AGENT_URL` using the direct
envelope JSON body.

## Workflow

The conversational graph is `START -> agent -> tools -> agent | END`. The LLM receives the API
Agent system prompt and can select `invoke_api_agent`; after a tool call, its result is appended
to the message state and the LLM decides whether another tool call is needed or the graph ends.
External callers normally use the HTTP tool directly, which runs the deterministic workflow
below without needing an LLM turn.

1. Validate `request.poc_id`, normalize missing run/task/trace IDs, and ensure the envelope
	targets `api_agent`.
2. Fetch the POC from the configured MongoDB database and collection. Load the four required
	Git-backed specification inputs from `spec_artifacts` and hydrate the generation request.
3. Create a task record, then generate the OpenAPI contract for `contract` and `generate` modes.
4. Generate backend files for `generate`, `code`, and `repair` modes. `code` and `repair` load
	the existing API contract first; `repair` also supplies the failure details to generation.
5. Store generated source under `backend/` on the POC branch, finish the task, and return an
	`AgentEnvelope` with artifact keys.
6. For successful code-producing modes, record the Git repository, branch, folder, and folder
	URL under `artifacts.code.git` in the POC document.

Unsupported modes and runtime errors finish the task with a structured failed response.

## HTTP Invocation

The published `invoke_api_agent` tool is an HTTP endpoint. Configure its URL as
`POC_API_AGENT_URL` in callers and send a JSON `AgentEnvelope` with bearer authentication when
`POC_AGENT_AUTH_TOKEN` is configured. Only `poc_id` is required; the agent creates omitted IDs
and loads requirements, specification, and existing API contracts from the POC record.

```bash
curl --request POST "$POC_API_AGENT_URL" \
	--header "Authorization: Bearer $POC_AGENT_AUTH_TOKEN" \
	--header "Content-Type: application/json" \
	--data '{
		"request": {
			"poc_id": "poc_0123456789abcdef0123456789",
			"caller": "coding_orchestrator",
			"agent": "api_agent",
			"tool": "invoke_api_agent",
			"mode": "generate",
			"params": {},
			"budget": {"max_tokens": 200000}
		}
	}'
```

`mode: "contract"` produces only the OpenAPI artifact. `mode: "generate"` produces both the
contract and backend; `code` and `repair` require an existing API contract. A successful response
contains the same request envelope plus artifact locations and the generated-code Git location.

```json
{
	"request": {"poc_id": "poc_0123456789abcdef0123456789", "task_id": "task_..."},
	"response": {
		"task_id": "task_...",
		"status": "succeeded",
		"result": {
			"contract_key": "backend/api_contract.yaml",
			"backend_prefix": "backend/",
			"git": {"repository_url": "https://github.com/org/repo", "branch": "poc/poc_...", "folder": "backend"}
		},
		"artifacts": [{"kind": "contract", "key": "backend/api_contract.yaml", "version": "v001"}],
		"error": null,
		"usage": {}
	}
}
```

Failures return `response.status: "failed"` and `response.error` with `code`, `message`,
`retryable`, and `detail`. Platform-specific invocation wrappers may wrap the returned envelope
in a `result` field; unwrap it before parsing the response.

## Testing

Run the isolated unit suite with in-memory repositories:

```bash
uv sync --group dev
uv run pytest
```

For an HTTP smoke test, start `agentic dev up`, create a POC with draft requirements and a POC
specification in the configured metadata store, then send the request above through the published
tool URL or the Playground. Confirm a `succeeded` response, that `artifacts.code.git` is updated
in MongoDB, and that the reported backend and contract keys exist in the artifact store.

## Local Development

Install Python 3.11+, `uv`, Docker, the `agentic` CLI, and authenticate GitHub for the local
platform stack. Run all commands from this directory, where `agent.yaml` is located.

Create a local `.env` file without committing it. It must provide `MONGODB_URI`, `GITHUB_TOKEN`,
`GITHUB_REPO_LINK`, `POC_METADATA_DATABASE`, `POC_METADATA_COLLECTION`, and the selected LLM
provider key (`OPENAI_API_KEY` for the default configuration). `POC_METADATA_DATABASE` defaults
to `PLATFORM_DATABASE` or `poc_builder`; `POC_METADATA_COLLECTION` defaults to `pocs`. The
request envelope's `poc_id` is queried against the selected collection's `pov_id` field. The POC
document must provide Git URLs for all four required `spec_artifacts` entries. The runtime uses
MongoDB and Git artifacts by default; set
`POC_METADATA_BACKEND=memory` and `POC_ARTIFACT_BACKEND=memory` only for isolated tests.

```bash
uv sync --group dev
uv run pytest
agentic dev up
```

Open the local Playground at the port in `dev.yaml` (`http://localhost:3013`). Use
`agentic dev logs` to inspect the services, `agentic dev restart` after agent changes, and
`agentic dev stop` when finished.

## Build And Deploy To Magenta

Authenticate and register this standalone agent project once. The platform creates the project
and workspace during `agentic init`.

```bash
agentic auth login
agentic init
```

Set the secrets required by [agent.yaml](agent.yaml) in the registered workspace. Do not put
secret values in source control or deployment manifests.

```bash
agentic secret set OPENAI_API_KEY
agentic secret set MONGODB_URI
agentic secret set POC_METADATA_DATABASE
agentic secret set POC_METADATA_COLLECTION
agentic secret set GITHUB_TOKEN
agentic secret set GITHUB_REPO_LINK
agentic secret list
```

Build and deploy from this directory:

```bash
agentic build
agentic deploy
```

Use the deployed workspace Playground or the platform invocation API to verify the deployment.
For agent-to-agent execution, configure the Coding Orchestrator's `POC_API_AGENT_URL` with this
deployment's published endpoint. The request body is a direct `AgentEnvelope`; the API Agent
hydrates missing request fields and reads `spec_artifacts` from the POC identified by matching
`request.poc_id` to `pov_id` in the configured MongoDB collection.

`agent.yaml` currently permits outbound access with `egress_mode: allow_all`. Replace that with
an allow list for production deployments when the destinations are known.

The wheel includes its own runtime, API implementation, generation services, tools, contracts,
and persistence adapters. It is independently buildable and deployable.