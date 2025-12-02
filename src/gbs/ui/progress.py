"""Progress monitoring for GBS build tasks

Provides live progress bars using tqdm for build tasks.
Only shows tasks that have started but not completed.
"""

from __future__ import annotations
import asyncio
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.build import BuildContext, BuildStep

from tqdm import tqdm

async def monitor_build_progress(context: BuildContext):
    """Monitor all tasks with live progress bars

    Wakes on central condition, reads state directly from tasks.
    Only displays tasks that have started but not completed.

    Args:
        context: Build context with progress tracking
    """
    # Check if stdout is a TTY
    if not sys.stdout.isatty():
        # Not a TTY, don't show progress bars
        return

    # Get all tasks (not Resources)
    from ..model.build import Task
    all_tasks = [step for step in context.steps if isinstance(step, Task)]

    if not all_tasks:
        # No tasks to monitor
        return

    # Dict of active progress bars
    task_bars: dict[str, tqdm] = {}

    # Overall progress bar
    overall_bar = tqdm(
        total=len(all_tasks),
        desc="Overall Build",
        position=0,
        unit=" tasks",
        leave=True
    )

    completed_count = 0
    bar_position = 1  # Start position for task bars (0 is overall)

    try:
        while completed_count < len(all_tasks):
            # Wait for ANY task to update
            await context.wait_for_progress_update()

            # Update/create/remove bars based on task state
            for task in all_tasks:
                task_name = task.name

                # Only show tasks that have started (progress > 0) but not completed
                should_show = task.progress_started is not None and not task.completed

                if should_show:
                    # Create bar if it doesn't exist
                    if task_name not in task_bars:
                        task_bars[task_name] = tqdm(
                            total=100,
                            desc=task.name[:40],  # Truncate long names
                            position=bar_position,
                            leave=False,
                            unit="%"
                        )
                        bar_position += 1

                    # Update bar
                    bar = task_bars[task_name]
                    bar.n = task.get_percentage()

                    if task.progress_message:
                        bar.set_postfix_str(task.progress_message[:50])

                    bar.refresh()

                elif task_name in task_bars:
                    # Task completed, close its bar
                    task_bars[task_name].close()
                    del task_bars[task_name]

            # Update overall count
            new_completed_count = sum(1 for task in all_tasks if task.completed)
            if new_completed_count > completed_count:
                overall_bar.update(new_completed_count - completed_count)
                completed_count = new_completed_count

    except asyncio.CancelledError:
        # Clean up all bars on cancellation
        for bar in task_bars.values():
            bar.close()
        overall_bar.close()
        raise
    finally:
        # Final cleanup
        for bar in task_bars.values():
            bar.close()
        overall_bar.close()


async def run_with_progress(context: BuildContext, outputs: list[BuildStep]):
    """Run build with progress monitoring

    Args:
        context: Build context
        outputs: List of output resources/tasks to build
    """
    # Start progress monitoring in parallel with build
    await asyncio.gather(
        asyncio.gather(*outputs),  # Build tasks
        monitor_build_progress(context)  # Progress monitoring
    )
