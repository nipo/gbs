"""JUnit XML formatter

Generates JUnit XML output from suite results for CI/CD integration.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

from ..model import SuiteResult, ProjectStatus


def write_junit_xml(result: SuiteResult, output_path: Path) -> None:
    """Write suite results as JUnit XML

    Generates standard JUnit XML format compatible with most CI/CD systems.

    Args:
        result: Suite execution result
        output_path: Path to write XML file

    Example:
        >>> result = await executor.build_suite()
        >>> write_junit_xml(result, Path("test-results/junit.xml"))
    """
    # Create root testsuites element
    testsuites = ET.Element('testsuites')
    testsuites.set('name', result.suite.name)
    testsuites.set('tests', str(result.total_projects))
    testsuites.set('failures', str(result.failed))
    testsuites.set('errors', str(result.errors))
    testsuites.set('skipped', str(result.skipped))
    testsuites.set('time', f'{result.duration:.3f}')
    testsuites.set('timestamp', datetime.now().isoformat())

    # Create a testsuite for each project
    for project_result in result.project_results:
        testsuite = ET.SubElement(testsuites, 'testsuite')
        testsuite.set('name', project_result.project.name)
        testsuite.set('tests', '1')
        testsuite.set('time', f'{project_result.duration:.3f}')

        # Count failures/errors/skips for this project. Unplannable
        # projects (no viable build plan on this host) are treated as
        # skipped rather than failed — they're a configuration miss
        # for the current environment, not a real build regression.
        if project_result.status == ProjectStatus.FAILURE:
            testsuite.set('failures', '1')
            testsuite.set('errors', '0')
            testsuite.set('skipped', '0')
        elif project_result.status == ProjectStatus.ERROR:
            testsuite.set('failures', '0')
            testsuite.set('errors', '1')
            testsuite.set('skipped', '0')
        elif project_result.status in (
            ProjectStatus.SKIPPED,
            ProjectStatus.UNPLANNABLE,
        ):
            testsuite.set('failures', '0')
            testsuite.set('errors', '0')
            testsuite.set('skipped', '1')
        else:  # SUCCESS
            testsuite.set('failures', '0')
            testsuite.set('errors', '0')
            testsuite.set('skipped', '0')

        # Add properties if available
        properties = ET.SubElement(testsuite, 'properties')

        # Add project path as property
        prop = ET.SubElement(properties, 'property')
        prop.set('name', 'project_path')
        prop.set('value', str(project_result.project.path))

        # Add output groups if specified
        if project_result.project.output_groups:
            prop = ET.SubElement(properties, 'property')
            prop.set('name', 'output_groups')
            prop.set('value', ','.join(project_result.project.output_groups))

        # Create testcase element
        testcase = ET.SubElement(testsuite, 'testcase')
        testcase.set('classname', project_result.project.name)
        testcase.set('name', 'build')
        testcase.set('time', f'{project_result.duration:.3f}')

        # Add failure/error/skip elements as needed
        if project_result.status == ProjectStatus.FAILURE:
            failure = ET.SubElement(testcase, 'failure')
            failure.set('message', 'Build failed')
            if project_result.error_message:
                failure.set('type', 'BuildFailure')
                failure.text = project_result.error_message

            # Add output tail if available
            if project_result.output_tail:
                failure.text = '\n'.join(project_result.output_tail)

        elif project_result.status == ProjectStatus.ERROR:
            error = ET.SubElement(testcase, 'error')
            error.set('message', 'Build error')
            if project_result.error_message:
                error.set('type', 'BuildError')
                error.text = project_result.error_message

            # Add output tail if available
            if project_result.output_tail:
                error.text = '\n'.join(project_result.output_tail)

        elif project_result.status == ProjectStatus.SKIPPED:
            skipped = ET.SubElement(testcase, 'skipped')
            skipped.set('message', project_result.error_message or 'Skipped')

        elif project_result.status == ProjectStatus.UNPLANNABLE:
            skipped = ET.SubElement(testcase, 'skipped')
            skipped.set('message', 'No viable build plan on this host')

        # Add system-out with log file reference if available
        if project_result.log_file:
            system_out = ET.SubElement(testcase, 'system-out')
            system_out.text = f"Log file: {project_result.log_file}"

    # Write XML to file
    tree = ET.ElementTree(testsuites)
    ET.indent(tree, space='  ')  # Pretty print

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tree.write(
        output_path,
        encoding='utf-8',
        xml_declaration=True
    )


__all__ = ['write_junit_xml']
