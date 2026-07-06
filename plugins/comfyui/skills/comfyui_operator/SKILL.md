---
name: ComfyUI Operator
description: Use when the user asks Lucode to inspect, connect to, or operate a local ComfyUI service or workflow.
---

# ComfyUI Operator

Use this skill only when the user asks for ComfyUI work.

Prefer these routes, in order:

1. Use the configured ComfyUI MCP server when `comfyui_graph` is available.
2. Use the embedded browser for visual inspection or UI-only workflows.
3. Ask for the ComfyUI service URL or launch path only when neither is configured.

Default local service URL: `http://127.0.0.1:8188`.

Do not assume a workflow is loaded. Read the current page, queue, or graph state before changing anything.
