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

## Integration Status

### ✅ Phase 1: Foundation (Complete)
- ✅ Created `gbs.ui` module with all core components
- ✅ Implemented message types (ToolMessage, LogMessage, Progress*, BuildStatus)
- ✅ Implemented FeedbackHub (async message router)
- ✅ Implemented NullHub (null object pattern - no "if hub" guards needed)
- ✅ Implemented SimpleBackend (plain text output with filtering)
- ✅ Added comprehensive tests

### ✅ Phase 2: ToolMessage Migration (Complete)
- ✅ Moved `ToolMessage` from `build/message.py` to `ui/messages.py`
- ✅ Added backward-compatibility shim in `build/message.py`
- ✅ Enhanced with comparison operators and timestamp
- ✅ All existing code works without changes

### ✅ Phase 3: CLI Integration (Complete)
- ✅ Wrapped CLI main() with FeedbackHub initialization
- ✅ Stored hub in Click context for subcommands
- ✅ Set global hub for deep call stack access
- ✅ Added cleanup via result_callback
- ✅ Mode-aware backend (respects verbose/debug flags)

### ✅ Phase 4: Build System Integration (Complete)
- ✅ Updated BuildContext.message_add() to emit via hub
- ✅ Tool messages now flow in real-time during builds
- ✅ Messages both stored locally AND emitted to hub
- ✅ DEBUG messages filtered correctly

### ✅ Phase 5: Suite Executor Integration (Complete)
- ✅ Replaced click.echo with hub.emit in suite commands
- ✅ Added hub.progress() for suite builds
- ✅ Emits BuildStatus for each project (started/success/error)
- ✅ Includes timing information

### ✅ Phase 6: Project Build Integration (Complete)
- ✅ Added progress tracking for individual projects
- ✅ Nested progress: Suite → Project → Output Group
- ✅ Clean hierarchical output with indentation
- ✅ Automatic progress hiding in verbose mode

### 🔄 Phase 7: Future Enhancements
- ⏳ Add RichBackend with colors and fancy progress bars
- ⏳ Convert remaining click.echo() calls to hub.log()
- ⏳ Add JSONBackend for tool integration
- ⏳ Add HTMLBackend for build reports

## Current Output Examples

### Normal Mode (with progress):
```
▸ Building suite 'gbs-examples' (6 projects)/6
ice40-synth: started
▸ Building pnr
✓ Done
ice40-synth: success (0.1s)
amba: started
▸ Building simulation
[NOTICE] (EX0101) Current top module is "boundary"
✓ Done
amba: success (0.1s)
✓ Done

Suite Results:
  Status: success
  Duration: 9.4s
```

### Debug Mode (verbose output):
```
INFO: Building suite 'gbs-examples' with 5 projects
ice40-synth: started
[DEBUG] set_device {-name} {GW5AT-60B}
[DEBUG] add_file {-type} {vhdl} {path/to/file.vhd}
[NOTICE] (EX0101) Current top module is "boundary"
ice40-synth: success (0.1s)
```

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
