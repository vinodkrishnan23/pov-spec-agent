"""OpenAPI and Express backend source generation."""

from __future__ import annotations

import json
from typing import Any


def generate_contract(
    title: str,
    user_stories: list[str] | list[dict[str, Any]],
    schema_design: dict[str, Any] | None = None,
) -> str:
    """Generate OpenAPI 3.1 with health and collection read operations."""
    stories = story_dicts(user_stories)
    story_ids = ", ".join(str(item["id"]) for item in stories)
    collections = (schema_design or {}).get("collections", [])
    paths = [
        f"""  /{collection["name"]}:
    get:
      operationId: list{pascal(str(collection["name"]))}
      x-user-story-ids: [{story_ids}]
      parameters:
        - in: query
          name: limit
          schema: {{type: integer, minimum: 1, maximum: 100, default: 20}}
        - in: query
          name: cursor
          schema: {{type: string}}
      responses:
        '200':
          description: Paginated results
          content:
            application/json:
              schema:
                type: object
                properties:
                  items: {{type: array, items: {{type: object}}}}
                  nextCursor: {{type: [string, 'null']}}
"""
        for collection in collections
    ]
    path_text = "".join(paths)
    return f"""openapi: 3.1.0
info:
  title: {title} API
  version: 0.1.0
servers:
  - url: /api
paths:
  /health:
    get:
      operationId: getHealth
      x-user-story-ids: [{story_ids}]
      responses:
        '200':
          description: Database is connected
          content:
            application/json:
              schema:
                type: object
                required: [status, db]
                properties:
                  status: {{type: string, const: ok}}
                  db: {{type: string, const: connected}}
{path_text}components:
  schemas:
    Error:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, message]
          properties:
            code: {{type: string}}
            message: {{type: string}}
"""


def generate_backend(
    contract: str,
    schema_design: dict[str, Any],
    repair_notes: Any | None = None,
  source_inputs: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate a complete installable Express backend honoring the contract."""
    if not contract.startswith("openapi: 3.1.0"):
        raise ValueError("api_contract.yaml must be OpenAPI 3.1.0")
    collections = [str(item["name"]) for item in schema_design.get("collections", [])]
    route_lines = "\n".join(
        f"app.get('/api/{name}', async (request, response, next) => {{\n"
        f"  try {{\n"
        f"    const limit = Math.min(Number(request.query.limit || 20), 100);\n"
        f"    const filter = request.query.cursor ? {{ _id: {{ $gt: new ObjectId(String(request.query.cursor)) }} }} : {{}};\n"
        f"    const items = await db.collection('{name}').find(filter).sort({{_id: 1}}).limit(limit).toArray();\n"
        f"    response.json({{items, nextCursor: items.length === limit ? String(items.at(-1)._id) : null}});\n"
        f"  }} catch (error) {{ next(error); }}\n"
        f"}});"
        for name in collections
    )
    server = f"""import express from 'express';
import mongoose from 'mongoose';
import {{ ObjectId }} from 'mongodb';

if (!process.env.MONGODB_URI) throw new Error('MONGODB_URI is required');
await mongoose.connect(process.env.MONGODB_URI);
const db = mongoose.connection.db;
const app = express();
app.use(express.json());
app.use((request, _response, next) => {{
  console.log(JSON.stringify({{method: request.method, path: request.path}}));
  next();
}});
app.get('/api/health', async (_request, response, next) => {{
  try {{
    await db.admin().ping();
    response.json({{status: 'ok', db: 'connected'}});
  }} catch (error) {{ next(error); }}
}});
{route_lines}
app.use((error, _request, response, _next) => {{
  console.error(error);
  response.status(500).json({{error: {{code: 'INTERNAL_ERROR', message: error.message}}}});
}});
app.listen(Number(process.env.PORT || 8080), '127.0.0.1');
"""
    package = {
        "name": "poc-backend",
        "private": True,
        "type": "module",
        "engines": {"node": ">=20"},
        "scripts": {"build": "node --check server.js", "start": "node server.js"},
        "dependencies": {
            "express": "^4.21.2",
            "mongoose": "^8.9.0",
            "mongodb": "^6.12.0",
        },
    }
    manifest = {
        "component": "backend",
        "runtime": "node20",
        "workdir": "backend",
        "entrypoints": {
            "install": ["npm install --omit=dev"],
            "build": ["npm run build"],
            "start": ["npm run start"],
            "healthcheck": {
                "type": "http",
                "url": "http://localhost:8080/api/health",
                "expect": 200,
            },
        },
        "env": {"required": ["MONGODB_URI", "PORT"], "optional": ["API_KEY"]},
        "ports": [8080],
    }
    files = {
        "backend/server.js": server,
        "backend/package.json": json.dumps(package, indent=2),
        "backend/.env.example": "MONGODB_URI=\nPORT=8080\n",
        "backend/component.manifest.json": json.dumps(manifest, indent=2),
        "backend/BACKEND_README.md": "# Backend\n\nGenerated from api_contract.yaml.\n",
    }
    if source_inputs:
        files["backend/generation-inputs.json"] = json.dumps(source_inputs, indent=2)
    if repair_notes is not None:
        files["backend/REPAIR_NOTES.md"] = "# Repair\n\n" + json.dumps(
            repair_notes, indent=2
        )
    return files


def story_dicts(value: Any) -> list[dict[str, Any]]:
    """Normalize plain and structured user stories."""
    values = value if isinstance(value, list) else [value]
    return [
        item
        if isinstance(item, dict)
        else {"id": f"us-{index:02d}", "title": str(item)}
        for index, item in enumerate(values, start=1)
    ]


def pascal(value: str) -> str:
    """Convert a collection identifier to an operation-name segment."""
    return "".join(part.capitalize() for part in value.replace("-", "_").split("_"))
