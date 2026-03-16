# OpenCode CLI Patterns Summary

## CLI Interface

```bash
# Main commands
opencode                    # Start TUI in current directory
opencode <directory>       # Start TUI in specific directory
opencode serve              # Start headless API server (port 4096)
opencode web                # Start server + open web interface
opencode completion         # Generate shell completions

# Flags
--help                     # Show all commands
--version                  # Show version
```

## Session Management

- **Session ID**: Auto-generated UUID for each conversation
- **Session state**: Stored in memory during session, persists in `~/.opencode/` between runs
- **History**: REPL history in `~/.opencode/history`
- **Sessions**: `client.session.create()`, `client.session.chat()`, `client.session.share()`

## Tool Calling (Agent Actions)

OpenCode uses a tool-calling pattern where the LLM returns structured tool calls:

```typescript
// Tool definitions sent to LLM
const tools = [
  { name: "Bash", description: "Run shell command", input: {...} },
  { name: "Edit", description: "Edit file", input: {...} },
  { name: "Read", description: "Read file", input: {...} },
  { name: "Write", description: "Write file", input: {...} },
  { name: "Glob", description: "Find files", input: {...} },
  { name: "Grep", description: "Search code", input: {...} },
  { name: "WebFetch", description: "Fetch URL", input: {...} },
  { name: "WebSearch", description: "Web search", input: {...} },
]

// LLM returns tool calls
{ tool: "Bash", input: { command: "ls -la", description: "..." } }
```

## Agent Behavior

**Two built-in agents** (switch with Tab):
- `build` — Default, full-access for development
- `plan` — Read-only for analysis/exploration

**Subagent**: `general` — For complex multi-step tasks, invoked with `@general`

**Flow**:
1. User sends message
2. LLM responds with text OR tool calls
3. Tools execute, results fed back to LLM
4. Loop until complete

**Tool execution**:
- Each tool has `run()` method returning `{ output, error }`
- Results streamed back to LLM as tool results
- Final text response displayed to user

## API Server

```bash
# Start server
opencode serve --port 4096

# Client SDK
import { createOpencodeClient } from "@opencode-ai/sdk"
const { client, server } = createOpencode()

# Session API
await client.session.create()
await client.session.chat({ path: session, body: {...} })
await client.session.share({ path: session })

# Stream events
fetch(`${server.url}/event`) // Server-Sent Events
```

## Build/Test Commands

```bash
# Install deps
bun install

# Dev mode
bun dev                    # TUI in packages/opencode
bun dev <directory>        # TUI in specific directory
bun dev serve              # Headless API server
bun dev web                # Server + web UI

# Build
./packages/opencode/script/build.ts --single

# Typecheck
bun typecheck             # From package dirs
```

## Key Patterns for Zener CLI

1. **Single CLI entry** → `zener setup`, `zener shell`, `zener run`
2. **Session loop** → screenshot → analyze → execute → verify → repeat
3. **Tool dispatch** → Action enum → executor handlers → macOS calls
4. **Live output** → LoopCallbacks for real-time step display
5. **History** → FileHistory for REPL, in-memory for agent context
