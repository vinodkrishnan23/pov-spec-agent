# POC Builder Frontend Agent

Generates the React/Vite/TypeScript frontend from approved stories and the executable API
contract. It preserves declared test IDs and supports bounded frontend repair.

## Runtime

The graph is defined in `src/main.py`. The deployment exposes `invoke_frontend_agent` as an
HTTP tool endpoint. It accepts an `AgentEnvelope`; only `request.poc_id` is mandatory. The
agent fetches the POC from the configured MongoDB database and collection, then loads
`frontend_contract` and `requirements` from the Git URLs in `spec_artifacts` before generation.

## Workflow

The conversational graph is `START -> agent -> tools -> agent | END`. The LLM receives the
Frontend Agent system prompt and can call `invoke_frontend_agent`; tool results return to the
message state until the LLM finishes. Direct HTTP callers use `invoke_frontend_agent` to execute
the deterministic path below without an LLM turn.

1. Validate `request.poc_id`, generate missing run/task/trace IDs, and ensure the envelope
	targets `frontend_agent`.
2. Fetch the POC from the configured MongoDB database and collection, load the required Git
	specification assets, and hydrate title, user stories, and the next code version.
3. Validate that the mode is `generate` or `repair`.
4. Generate the React/Vite files. In repair mode, the failure details are included in the
	generation input.
5. Persist each generated file under `frontend/` on the POC branch, finish the task, and return
	their keys in a successful `AgentEnvelope`.

Missing API contracts, unsupported modes, and generation errors are persisted as structured task
failures in the response envelope.

## HTTP Invocation

The published `invoke_frontend_agent` tool is an HTTP endpoint. Configure its URL as
`POC_FRONTEND_AGENT_URL` in callers and send a JSON `AgentEnvelope` with bearer authentication
when `POC_AGENT_AUTH_TOKEN` is configured. The only required request field is `poc_id`; the
agent creates omitted IDs and fetches the title, user stories, and API contract for that POC.

```bash
curl --request POST "$POC_FRONTEND_AGENT_URL" \
	--header "Authorization: Bearer $POC_AGENT_AUTH_TOKEN" \
	--header "Content-Type: application/json" \
	--data '{
		"request": {
			"poc_id": "poc_0123456789abcdef0123456789",
			"caller": "coding_orchestrator",
			"agent": "frontend_agent",
			"tool": "invoke_frontend_agent",
			"mode": "generate",
			"params": {},
			"budget": {"max_tokens": 200000}
		}
	}'
```

Successful responses use the same envelope and include generated artifact keys. Failures return
`response.status: "failed"` with a structured error rather than an HTTP-success result being
silently treated as generated code.

```json
{
	"request": {"poc_id": "poc_0123456789abcdef0123456789", "task_id": "task_..."},
	"response": {
		"task_id": "task_...",
		"status": "succeeded",
		"result": {"frontend_prefix": "frontend/", "files": ["frontend/src/App.tsx"]},
		"artifacts": [{"kind": "code", "key": "frontend/src/App.tsx", "version": "v001"}],
		"error": null,
		"usage": {}
	}
}
```

For `repair`, set `mode` to `repair`; the referenced POC must already have a current code
version and API contract. Platform-specific invocation wrappers may wrap the returned JSON in a
`result` field; callers should unwrap that field before decoding the envelope.

## Testing

Run the isolated unit suite with the in-memory metadata and artifact backends:

```bash
uv sync --group dev
uv run pytest
```

For an HTTP smoke test, start the local platform with `agentic dev up`, create a POC with an API
contract in the configured MongoDB/artifact backend, then send the request above to the published
tool URL or invoke `invoke_frontend_agent` from the Playground. Verify a `succeeded` response
and that every returned artifact key is present under the reported `frontend_prefix`.

## Local Development

Install Python 3.11+, `uv`, Docker, and the `agentic` CLI, then authenticate GitHub for local
development. Run commands from this directory, which contains this agent's `agent.yaml`.

Create an uncommitted `.env` with `OPENAI_API_KEY`, `MONGODB_URI`, `GITHUB_TOKEN`,
`GITHUB_REPO_LINK`, `POC_METADATA_DATABASE`, and `POC_METADATA_COLLECTION`. The database
defaults to `PLATFORM_DATABASE` or `poc_builder`, and the collection defaults to `pocs`. The POC
request envelope's `poc_id` is queried against the selected collection's `pov_id` field. The POC
document must expose Git URLs for `spec_artifacts.frontend_contract` and
`spec_artifacts.requirements`. MongoDB supplies POC metadata and Git stores generated files in
the branch-root `frontend/` folder. Local unit tests can use the in-memory backends, but deployed
runs require the configured MongoDB and Git credentials.

```bash
uv sync --group dev
uv run pytest
agentic dev up
```

Open the local Playground at the port in `dev.yaml` (`http://localhost:3016`). Use
`agentic dev logs` to troubleshoot, `agentic dev restart` after edits, and `agentic dev stop`
when the local stack is no longer needed.

## Build And Deploy To Magenta

Connect the standalone project to Magenta once, then configure the secrets declared in
[agent.yaml](agent.yaml).

```bash
agentic auth login
agentic init
agentic secret set OPENAI_API_KEY
agentic secret set MONGODB_URI
agentic secret set POC_METADATA_DATABASE
agentic secret set POC_METADATA_COLLECTION
agentic secret set GITHUB_TOKEN
agentic secret set GITHUB_REPO_LINK
agentic secret list
```

Build and deploy from this agent directory:

```bash
agentic build
agentic deploy
```

Verify the deployed workspace through the Magenta Playground or platform invocation API, then
set the Coding Orchestrator's `POC_FRONTEND_AGENT_URL` to this deployment's published endpoint.
Inter-agent calls use the `AgentEnvelope` contract, so the frontend agent remains independently
deployable and does not need the Coding Orchestrator's source package. It reads `spec_artifacts`
after matching `request.poc_id` to `pov_id` in the configured MongoDB collection. The current
[agent.yaml](agent.yaml) allows all outbound traffic; replace it with a production allow list
once the permitted destinations are established.

The wheel includes root-level application modules and a `tools` package for persistence
adapters. It is independently buildable and deployable.