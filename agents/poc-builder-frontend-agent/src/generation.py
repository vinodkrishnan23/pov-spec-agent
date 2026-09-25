"""Deterministic React/Vite source generation for the Frontend Agent."""

from __future__ import annotations

import json
from typing import Any


def generate_frontend(
    title: str,
    user_stories: list[str] | list[dict[str, Any]],
    api_contract: str,
    repair_notes: Any | None = None,
    source_inputs: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Generate a buildable React frontend from user stories and OpenAPI."""
    if not api_contract.startswith("openapi: 3.1.0"):
        raise ValueError("api_contract.yaml must be OpenAPI 3.1.0")
    stories = _normalize_stories(user_stories)
    sections = "\n".join(_story_section(story) for story in stories)
    app = f"""import {{ apiGet }} from './api/client';

export default function App() {{
  void apiGet;
  return (
    <main>
      <h1>{_tsx_text(title)}</h1>
{sections}
    </main>
  );
}}
"""
    package = {
        "name": "poc-frontend",
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {"build": "tsc -b && vite build"},
        "dependencies": {
            "@vitejs/plugin-react": "latest",
            "vite": "latest",
            "typescript": "latest",
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        },
        "devDependencies": {"@types/react": "latest", "@types/react-dom": "latest"},
    }
    manifest = {
        "component": "frontend",
        "runtime": "node20",
        "workdir": "frontend",
        "entrypoints": {"install": ["npm install"], "build": ["npm run build"]},
        "env": {"required": ["VITE_API_BASE_URL"]},
        "static_dir": "dist",
        "publish": {
            "type": "nginx_static",
            "api_proxy": {"path": "/api", "upstream": "http://127.0.0.1:8080"},
        },
    }
    files = {
        "frontend/src/App.tsx": app,
        "frontend/src/api/client.ts": """const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api';

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}
""",
        "frontend/src/main.tsx": "import React from 'react';\nimport {createRoot} from 'react-dom/client';\nimport App from './App';\ncreateRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);\n",
        "frontend/src/vite-env.d.ts": '/// <reference types="vite/client" />\n',
        "frontend/index.html": f'<div id="root"></div><script type="module" src="/src/main.tsx"></script><title>{title}</title>',
        "frontend/tsconfig.json": json.dumps(
            {
                "compilerOptions": {
                    "jsx": "react-jsx",
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "Bundler",
                    "strict": True,
                },
                "include": ["src"],
            },
            indent=2,
        ),
        "frontend/package.json": json.dumps(package, indent=2),
        "frontend/component.manifest.json": json.dumps(manifest, indent=2),
        "frontend/FRONTEND_README.md": "# Frontend\n\nSet `VITE_API_BASE_URL=/api` at build time.\n",
    }
    if source_inputs:
        files["frontend/generation-inputs.json"] = json.dumps(source_inputs, indent=2)
    if repair_notes is not None:
        files["frontend/REPAIR_NOTES.md"] = "# Repair\n\n" + json.dumps(
            repair_notes, indent=2
        )
    return files


def _normalize_stories(value: list[str] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        if isinstance(item, dict)
        else {"id": f"us-{index:02d}", "title": str(item)}
        for index, item in enumerate(value, start=1)
    ]


def _story_section(story: dict[str, Any]) -> str:
    story_id = str(story.get("id", "user-story"))
    testids = [str(value) for value in story.get("testids", [])] or [story_id]
    markers = "".join(f'<div data-testid="{testid}" />' for testid in testids)
    return f'      <section data-testid="{story_id}"><h2>{_tsx_text(str(story.get("title", story_id)))}</h2>{markers}</section>'


def _tsx_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
