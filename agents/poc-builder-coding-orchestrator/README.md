# POC Builder Coding Orchestrator

Coordinates approved code runs, assembles versioned bundles, and routes component repairs. In
separated mode it invokes API, Data Seeding, and Frontend agents over HTTP instead of importing
their services in-process.

## Runtime

Set each downstream agent's URL, client ID, and client secret: `POC_API_AGENT_URL`,
`POC_API_AGENT_CLIENT_ID`, `POC_API_AGENT_CLIENT_SECRET`,
`POC_DATA_SEEDING_AGENT_URL`, `POC_DATA_SEEDING_AGENT_CLIENT_ID`,
`POC_DATA_SEEDING_AGENT_CLIENT_SECRET`, `POC_FRONTEND_AGENT_URL`,
`POC_FRONTEND_AGENT_CLIENT_ID`, and `POC_FRONTEND_AGENT_CLIENT_SECRET`. Each URL must be the
agent's Magenta `invokeStream` URL. Set `POC_METADATA_DATABASE` and
`POC_METADATA_COLLECTION` to select the MongoDB database and POC collection; they default to
`PLATFORM_DATABASE` or `poc_builder` and `pocs`. The request envelope's `poc_id` is queried
against the MongoDB document field `pov_id`.
The graph is defined directly in `src/main.py`. Workflow logic is in `src/orchestrator.py`,
the HTTP adapter is in `src/agent_client.py`, and reusable infrastructure is under `src/tools/`.

## Workflow

The conversational graph is `START -> agent -> tools -> agent | END`. The LLM can select any
registered orchestration tool; after each call its result is added to the message state before the
LLM either makes another tool call or ends. The lifecycle HTTP tools execute the following
deterministic code-run workflow.

1. `start_code_run` and `invoke_coding_orchestrator` retrieve the POC document by `pov_id` and
	verify that `spec_artifacts` has Git links for `data_model`, `query_patterns`,
	`api_contract`, `frontend_contract`, and `requirements`. Missing links return
	`REQUIRED_SPEC_ARTIFACT_LINKS_MISSING` before a task or run is created. The orchestrator only
	validates these links; it does not download the Git specification files.
2. `start_code_run` verifies the required specification artifact links and queues a run.
	`process_code_run` then creates the orchestration envelope for that queued run.
3. Load the approved specification artifacts, schema design, query patterns, POC requirements,
	and user stories. Allocate the next code version.
4. Call the API Agent with `mode: "contract"`, then call it again with `mode: "code"` using the
	returned contract key.
5. Call Data Seeding and Frontend agents concurrently. Each request carries the same POC/run
	context and the inputs required by that component.
6. Collect all generated artifacts and run the text guardrail scan. A violation fails the run
	before any bundle is marked ready.
7. Write `poc.manifest.json` and `bundle.tar.gz`, update the POC to `code_ready`, store the
	current code version and artifact references, and return presigned specification and bundle
	URLs.

`repair_component` follows the repair route for the failed component and returns a structured
result. Any child-agent, approval, artifact, or guardrail failure marks the run and POC failed
and returns `response.status: "failed"`.

## LLM Prompt

The Coding Orchestrator has one static LLM prompt. It is supplied as a system message on every
graph turn before the accumulated user, assistant, and tool-result messages:

```text
You are the Coding Orchestrator. Start and process code runs when required
specification artifact links are present, coordinate contract-first component generation, and
perform bounded component repairs. Never expose credentials.
```

There are no additional generation prompts in this agent. The deterministic implementation in
`src/orchestrator.py` performs artifact loading, child-agent invocation, guardrail scanning, and
bundle creation directly. The LLM selects among the registered lifecycle tools
(`invoke_coding_orchestrator`, `start_code_run`, `process_code_run`, and `repair_component`),
then receives each tool result in the message history before its next decision.

## HTTP Invocation

The orchestrator publishes four HTTP tools: `invoke_coding_orchestrator`, `start_code_run`,
`process_code_run`, and `repair_component`. Before invoking a lifecycle tool, configure the URL,
client ID, and client secret for every child agent. For each child call, the orchestrator requests
an access token from `https://agentengine.mongodb.com/api/v1/oauth/token` using that child
agent's client credentials, then sends the serialized `AgentEnvelope` as the `message` field to
the configured `invokeStream` URL with that access token as a bearer token. The three client
credential pairs are intentionally independent.

`invoke_coding_orchestrator` currently accepts the envelope as a JSON-encoded string under
`envelope_json`. Its inner envelope only requires `request.poc_id`; the other fields make the
caller, action, and execution budget explicit.

```bash
curl --request POST "$POC_CODING_ORCHESTRATOR_URL" \
	--header "Authorization: Bearer $POC_AGENT_AUTH_TOKEN" \
	--header "Content-Type: application/json" \
	--data '{
		"envelope_json": "{\"request\":{\"poc_id\":\"poc_0123456789abcdef0123456789\",\"caller\":\"chat_agent\",\"agent\":\"coding_orchestrator\",\"tool\":\"invoke_coding_orchestrator\",\"mode\":\"generate\",\"params\":{},\"budget\":{\"max_tokens\":200000}}}"
	}'
```

The tool returns an `AgentEnvelope` JSON string. Decode that string, or unwrap a platform
`result` field first when the HTTP runtime supplies one. A successful run contains code/artifact
results; a failed run has `response.status: "failed"` and a structured `response.error`.

```json
{
	"request": {"poc_id": "poc_0123456789abcdef0123456789", "task_id": "task_..."},
	"response": {
		"task_id": "task_...",
		"status": "succeeded",
		"result": {"code_version": "v001"},
		"artifacts": [{"kind": "bundle", "key": "pocs/poc_.../code/v001/bundle.tar.gz", "version": "v001"}],
		"error": null,
		"usage": {}
	}
}
```

For the lifecycle-specific tools, use these request shapes: `start_code_run` accepts
`{"poc_id": "...", "spec_version": "vNNN"}`, `process_code_run` accepts
`{"run_id": "run_..."}`, and `repair_component` accepts
`{"failure_json": "<serialized FailureReport>"}`. `start_code_run` returns the queued run,
which can then be passed to `process_code_run`.

## Testing

Run the isolated unit suite:

```bash
uv sync --group dev
uv run pytest
```

For an end-to-end HTTP smoke test, deploy the API, Data Seeding, and Frontend agents, configure
each agent's `invokeStream` URL and dedicated service-account credentials, then start
`agentic dev up` for this agent. Create an approved POC specification, call `start_code_run`,
call `process_code_run` with the returned run ID, and verify that the response succeeds and its
bundle/artifact keys are retrievable.

## Local Development

Install Python 3.11+, `uv`, Docker, and the `agentic` CLI. Authenticate GitHub before starting
the local platform stack. Run commands from this directory, where `agent.yaml` lives.

Create an uncommitted `.env` with `OPENAI_API_KEY`, `MONGODB_URI`, `GITHUB_TOKEN`,
`GITHUB_REPO_LINK`, `POC_METADATA_DATABASE`, `POC_METADATA_COLLECTION`, and one URL/client
ID/client secret set for each deployed agent: `POC_API_AGENT_URL`,
`POC_API_AGENT_CLIENT_ID`, `POC_API_AGENT_CLIENT_SECRET`, `POC_DATA_SEEDING_AGENT_URL`,
`POC_DATA_SEEDING_AGENT_CLIENT_ID`, `POC_DATA_SEEDING_AGENT_CLIENT_SECRET`,
`POC_FRONTEND_AGENT_URL`, `POC_FRONTEND_AGENT_CLIENT_ID`, and
`POC_FRONTEND_AGENT_CLIENT_SECRET`. These values are required when the orchestrator runs a
lifecycle tool; it performs the Agent Engine OAuth exchange and calls each child service over
its published `invokeStream` endpoint instead of importing child-agent code.

```bash
uv sync --group dev
uv run pytest
agentic dev up
```

Open the Playground at the port configured in `dev.yaml` (`http://localhost:3014`). Use
`agentic dev logs` to inspect the local services, `agentic dev restart` after code changes, and
`agentic dev stop` to tear the stack down.

## Build And Deploy To Magenta

Register this project with Magenta once, then set the runtime secrets in the workspace.

```bash
agentic auth login
agentic init
agentic secret set OPENAI_API_KEY
agentic secret set MONGODB_URI
agentic secret set GITHUB_TOKEN
agentic secret set GITHUB_REPO_LINK
agentic secret set POC_API_AGENT_URL
agentic secret set POC_API_AGENT_CLIENT_ID
agentic secret set POC_API_AGENT_CLIENT_SECRET
agentic secret set POC_DATA_SEEDING_AGENT_URL
agentic secret set POC_DATA_SEEDING_AGENT_CLIENT_ID
agentic secret set POC_DATA_SEEDING_AGENT_CLIENT_SECRET
agentic secret set POC_FRONTEND_AGENT_URL
agentic secret set POC_FRONTEND_AGENT_CLIENT_ID
agentic secret set POC_FRONTEND_AGENT_CLIENT_SECRET
agentic secret list
```

Build and deploy the standalone orchestrator from this directory:

```bash
agentic build
agentic deploy
```

After deployment, verify the workspace in the Magenta Playground. Configure each downstream URL
with its independently deployed agent's published `invokeStream` endpoint and give it a distinct
client ID/client secret pair before starting a code run. The current [agent.yaml](agent.yaml) has
`egress_mode: allow_all`; restrict it to the required platform and LLM destinations before a
production deployment.

The wheel includes its complete graph, orchestration service, remote client, contracts, and
storage adapters. API, Data Seeding, and Frontend implementations are intentionally absent;
their endpoint variables are required when a lifecycle tool executes. No parent project package
is required.