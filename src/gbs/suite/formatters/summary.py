"""Summary JSON formatter

Generates JSON summary from suite results for programmatic access.
"""

import json
from pathlib import Path
from datetime import datetime

from ..model import SuiteResult, ProjectStatus, SuiteStatus


def write_summary_json(result: SuiteResult, output_path: Path) -> None:
    """Write suite results as JSON summary

    Generates comprehensive JSON output with all result details.

    Args:
        result: Suite execution result
        output_path: Path to write JSON file

    Example:
        >>> result = await executor.build_suite()
        >>> write_summary_json(result, Path("test-results/summary.json"))
    """
    # Build summary dictionary
    summary = {
        'suite': result.suite.name,
        'status': result.status.value,
        'duration': round(result.duration, 3),
        'timestamp': datetime.now().isoformat(),
        'statistics': {
            'total': result.total_projects,
            'successful': result.successful,
            'failed': result.failed,
            'errors': result.errors,
            'skipped': result.skipped
        },
        'projects': []
    }

    # Add project results
    for project_result in result.project_results:
        project_data = {
            'name': project_result.project.name,
            'status': project_result.status.value,
            'duration': round(project_result.duration, 3),
            'path': str(project_result.project.path)
        }

        # Add optional fields if present
        if project_result.project.output_groups:
            project_data['output_groups'] = project_result.project.output_groups

        if project_result.project.depends_on:
            project_data['depends_on'] = project_result.project.depends_on

        if project_result.project.tags:
            project_data['tags'] = project_result.project.tags

        if project_result.log_file:
            project_data['log_file'] = str(project_result.log_file)

        if project_result.error_message:
            project_data['error_message'] = project_result.error_message

        if project_result.output_tail:
            project_data['output_tail'] = project_result.output_tail

        if project_result.source_files:
            # Convert Path objects to strings
            project_data['source_files'] = sorted(
                str(f) for f in project_result.source_files
            )
            project_data['source_file_count'] = len(project_result.source_files)

        summary['projects'].append(project_data)

    # Write JSON to file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


__all__ = ['write_summary_json']
