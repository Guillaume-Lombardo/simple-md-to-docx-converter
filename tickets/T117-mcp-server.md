---
ticket: T117
linear_id: G1L-646
linear_url: https://linear.app/g1lom/issue/G1L-646/t117-add-an-mcp-server-for-ai-agents
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T117 - Add an MCP server for AI agents

## Objective

Expose conversion, reverse conversion and template listing to AI agents through the Model Context Protocol, built on the public HTTP API.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- MCP tools: list templates, convert Markdown, check job status, download result, and reverse-convert a document; all calls go through the HTTP API.
- Authentication uses T114 tokens; no session cookies or passwords are handled by the MCP server.
- Packaged with the CLI (for example `markweave mcp`), documented for common MCP clients, and tested against a real server in both profiles.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification
- T114 - Add scoped revocable API tokens

## Blocking points

- Requires decision D10.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-646.
