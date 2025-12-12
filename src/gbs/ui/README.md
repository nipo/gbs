# GBS UI Module

Centralized system for all user-facing output in GBS.

## Architecture

```
Message Types → FeedbackHub → Backend → Output
```

**Key Components:**

1. **Message Types** (`messages.py`): Structured message objects representing all user feedback
2. **FeedbackHub** (`hub.py`): Central async router that processes messages sequentially
3. **Backends** (`backends/`): Pluggable renderers that format messages for different outputs

## Design Principles

1. **Single Source of Truth**: All user output flows through one system
2. **Separation of Concerns**: Message generation is separate from rendering
3. **Backend Agnostic**: Same messages can be rendered as text, Rich output, JSON, HTML, etc.
4. **Async Native**: Built for asyncio with proper task management
5. **Thread Safe**: Message emission is safe from any context

## Message Types

### ToolMessage
Compiler-style output with file:line:column format
```python
ToolMessage(
    severity=MessageSeverity.ERROR,
    message="Missing semicolon",
    file_path=Path("foo.vhd"),
    line=42,
    column=10
)
# Output: foo.vhd:42:10:ERROR: Missing semicolon
```

### LogMessage
General purpose logging
```python
LogMessage(level="info", message="Starting build", source="Project")
# Output: [INFO] Project: Starting build
```

### Progress Messages
Hierarchical progress tracking via context managers
```python
async with hub.progress("Building project", total=10) as prog:
    for i in range(10):
        await do_work()
        prog.update(i + 1, "Step completed")
```

### BuildStatus
High-level build status
```python
BuildStatus(status="success", target="my-project", duration=1.5)
# Output: my-project: success (1.5s)
```

## Usage

### Basic Setup

```python
from gbs.ui import FeedbackHub, SimpleBackend

async def main():
    async with FeedbackHub(SimpleBackend()) as hub:
        hub.log("info", "Starting build")

        async with hub.progress("Building") as prog:
            # ... do work ...
            prog.update(message="Analyzing...")
```

### Global Hub

For code that doesn't have direct hub access:

```python
from gbs.ui import set_global_hub, emit, LogMessage

# In main():
set_global_hub(hub)

# Deep in call stack:
emit(LogMessage(level="info", message="Something happened"))
```

### Progress Nesting

Progress tasks automatically nest based on context:

```python
async with hub.progress("Building suite") as suite:
    for project in projects:
        async with hub.progress(f"Building {project.name}") as proj:
            for task in tasks:
                await task.run()
                proj.update(message=f"Completed {task.name}")
```

## Backends

### SimpleBackend

Plain text output for terminals and CI. No colors, simple progress indicators.

```python
backend = SimpleBackend(
    output=sys.stdout,
    error=sys.stderr,
    show_progress=True,
    show_debug=False
)
```

### RichBackend (Future)

Fancy terminal output with colors, live progress bars, and formatting.

```python
backend = RichBackend(
    use_colors=True,
    show_progress_bars=True
)
```

### JSONBackend (Future)

Structured JSON output for tool integration.

```python
backend = JSONBackend(output_file="build.json")
```

## Migration Path

### Phase 1: Foundation (Complete)
- ✅ Create `gbs.ui` module
- ✅ Implement message types
- ✅ Implement FeedbackHub
- ✅ Implement SimpleBackend
- ✅ Test basic functionality

### Phase 2: Move ToolMessage
- Move `ToolMessage` from `build/message.py` to `ui/messages.py`
- Add backward-compat import in `build/message.py`
- Update all imports gradually

### Phase 3: Integrate with CLI
- Wrap CLI main() with FeedbackHub
- Store hub in Click context
- Set global hub for deep call stacks

### Phase 4: Convert Build System
- Update BuildContext to use hub
- Convert task output to use hub
- Replace direct prints with hub.emit()

### Phase 5: Convert Suite Executor
- Replace click.echo with hub.emit
- Use hub.progress for suite builds
- Emit BuildStatus messages

### Phase 6: Add RichBackend
- Implement RichBackend with colors and progress bars
- Auto-detect terminal capabilities
- Fall back to SimpleBackend for non-TTY

## Example: Full Integration

```python
# In gbs/cli/main.py

from gbs.ui import FeedbackHub, SimpleBackend, set_global_hub

@click.group()
@click.pass_context
async def cli(ctx):
    # Detect backend
    if sys.stdout.isatty():
        backend = RichBackend()  # Future
    else:
        backend = SimpleBackend()

    # Initialize hub
    async with FeedbackHub(backend) as hub:
        set_global_hub(hub)
        ctx.obj["feedback_hub"] = hub

        # Run command
        await ctx.invoke(...)
```

```python
# In gbs/suite/executor.py

async def build_suite(self):
    hub = get_global_hub()

    async with hub.progress(f"Building suite '{self.suite.name}'", total=len(projects)) as prog:
        for project in projects:
            async with hub.progress(f"Building {project.name}") as proj_prog:
                result = await self._build_project(project)

                hub.emit(BuildStatus(
                    status="success" if result.success else "failure",
                    target=project.name,
                    duration=result.duration
                ))
```

## Benefits

1. **Consistency**: All output uses the same format and routing
2. **Flexibility**: Easy to add new backends (JSON, HTML, etc.)
3. **Testability**: Can capture messages for testing instead of printing
4. **Performance**: Async processing prevents output blocking builds
5. **Cleanliness**: No more scattered print/click.echo calls
6. **Progress Tracking**: First-class support for nested progress

## Testing

See `/tmp/test_ui.py` for comprehensive examples of all message types and progress tracking.
