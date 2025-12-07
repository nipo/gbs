"""Tests for GBS Suite Executor

Tests suite execution algorithms including topological sort, dependency levels,
and basic execution scenarios.
"""

import pytest
from pathlib import Path
import tempfile

from gbs.suite.model import (
    Suite, SuiteSettings, ProjectReference, SuiteStatus, ProjectStatus
)
from gbs.suite.executor import SuiteExecutor, ExecutionError


class TestTopologicalSort:
    """Test topological sorting algorithm"""

    def test_simple_linear_order(self):
        """Test simple linear dependency chain"""
        projects = [
            ProjectReference(name='c', path=Path('c'), depends_on=['b']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()

        # Should be sorted: a, b, c
        assert [p.name for p in sorted_projects] == ['a', 'b', 'c']

    def test_parallel_branches(self):
        """Test independent parallel branches"""
        projects = [
            ProjectReference(name='d', path=Path('d'), depends_on=['b', 'c']),
            ProjectReference(name='c', path=Path('c'), depends_on=['a']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()

        # 'a' must be first, 'd' must be last
        # 'b' and 'c' can be in any order but after 'a' and before 'd'
        names = [p.name for p in sorted_projects]
        assert names[0] == 'a'
        assert names[-1] == 'd'
        assert set(names[1:3]) == {'b', 'c'}

    def test_diamond_dependency(self):
        """Test diamond-shaped dependency graph"""
        projects = [
            ProjectReference(name='d', path=Path('d'), depends_on=['b', 'c']),
            ProjectReference(name='c', path=Path('c'), depends_on=['a']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()

        # Valid orderings: [a, b, c, d] or [a, c, b, d]
        names = [p.name for p in sorted_projects]
        assert names[0] == 'a'
        assert names[-1] == 'd'

    def test_no_dependencies(self):
        """Test projects with no dependencies"""
        projects = [
            ProjectReference(name='c', path=Path('c')),
            ProjectReference(name='a', path=Path('a')),
            ProjectReference(name='b', path=Path('b')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()

        # Should be alphabetically sorted (due to sort() in algorithm)
        assert [p.name for p in sorted_projects] == ['a', 'b', 'c']

    def test_circular_dependency_detected(self):
        """Test that circular dependencies are detected"""
        projects = [
            ProjectReference(name='a', path=Path('a'), depends_on=['b']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        with pytest.raises(ExecutionError, match="Circular dependency"):
            executor._topological_sort()

    def test_skipped_projects_ignored(self):
        """Test that skipped projects are not included in sort"""
        projects = [
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a'), skip=True),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()

        # Only 'b' should be included (even though it depends on skipped 'a')
        assert [p.name for p in sorted_projects] == ['b']


class TestDependencyLevels:
    """Test dependency level grouping"""

    def test_simple_levels(self):
        """Test simple level grouping"""
        projects = [
            ProjectReference(name='c', path=Path('c'), depends_on=['b']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()
        levels = executor._get_dependency_levels(sorted_projects)

        # Should have 3 levels, one project per level
        assert len(levels) == 3
        assert [p.name for p in levels[0]] == ['a']
        assert [p.name for p in levels[1]] == ['b']
        assert [p.name for p in levels[2]] == ['c']

    def test_parallel_in_level(self):
        """Test projects that can run in parallel"""
        projects = [
            ProjectReference(name='d', path=Path('d'), depends_on=['b', 'c']),
            ProjectReference(name='c', path=Path('c'), depends_on=['a']),
            ProjectReference(name='b', path=Path('b'), depends_on=['a']),
            ProjectReference(name='a', path=Path('a')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()
        levels = executor._get_dependency_levels(sorted_projects)

        # Should have 3 levels: [a], [b,c], [d]
        assert len(levels) == 3
        assert [p.name for p in levels[0]] == ['a']
        assert set(p.name for p in levels[1]) == {'b', 'c'}
        assert [p.name for p in levels[2]] == ['d']

    def test_no_dependencies_single_level(self):
        """Test all independent projects in one level"""
        projects = [
            ProjectReference(name='c', path=Path('c')),
            ProjectReference(name='a', path=Path('a')),
            ProjectReference(name='b', path=Path('b')),
        ]
        suite = Suite(name='test', projects=projects)
        executor = SuiteExecutor(suite)

        sorted_projects = executor._topological_sort()
        levels = executor._get_dependency_levels(sorted_projects)

        # All projects should be in one level
        assert len(levels) == 1
        assert set(p.name for p in levels[0]) == {'a', 'b', 'c'}


class TestProjectFileFinding:
    """Test finding project files"""

    def test_find_file_path(self):
        """Test finding project when path is a file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_file = tmppath / "project.gbs.yaml"
            project_file.write_text("name: test")

            suite = Suite(name='test', projects=[])
            executor = SuiteExecutor(suite)

            found = executor._find_project_file(project_file)
            assert found == project_file

    def test_find_dir_path(self):
        """Test finding project when path is a directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_dir = tmppath / "myproject"
            project_dir.mkdir()
            project_file = project_dir / "project.gbs.yaml"
            project_file.write_text("name: test")

            suite = Suite(name='test', projects=[])
            executor = SuiteExecutor(suite)

            found = executor._find_project_file(project_dir)
            assert found == project_file

    def test_missing_project_file_in_dir(self):
        """Test error when directory doesn't contain project file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            project_dir = tmppath / "myproject"
            project_dir.mkdir()

            suite = Suite(name='test', projects=[])
            executor = SuiteExecutor(suite)

            with pytest.raises(ExecutionError, match="No project.gbs.yaml found"):
                executor._find_project_file(project_dir)

    def test_nonexistent_path(self):
        """Test error when path doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            nonexistent = tmppath / "nonexistent"

            suite = Suite(name='test', projects=[])
            executor = SuiteExecutor(suite)

            with pytest.raises(ExecutionError, match="Project path does not exist"):
                executor._find_project_file(nonexistent)


class TestSuiteResult:
    """Test suite result aggregation

    Note: These are basic structural tests. Full integration tests
    would require actual project files and builds.
    """

    def test_result_status_calculation(self):
        """Test that result status is correctly determined"""
        # This tests the data model, not the executor directly
        from gbs.suite.model import SuiteResult, ProjectResult, ProjectReference

        proj_ref = ProjectReference(name='test', path=Path('test'))

        # All success -> success
        result = SuiteResult(
            suite=Suite(name='test', projects=[]),
            status=SuiteStatus.SUCCESS,
            duration=1.0,
            project_results=[
                ProjectResult(project=proj_ref, status=ProjectStatus.SUCCESS, duration=1.0)
            ],
            total_projects=1,
            successful=1,
            failed=0,
            errors=0,
            skipped=0
        )
        assert result.status == SuiteStatus.SUCCESS

        # Any error -> error
        result_with_error = SuiteResult(
            suite=Suite(name='test', projects=[]),
            status=SuiteStatus.ERROR,
            duration=1.0,
            project_results=[
                ProjectResult(project=proj_ref, status=ProjectStatus.ERROR, duration=1.0, error_message="Test error")
            ],
            total_projects=1,
            successful=0,
            failed=0,
            errors=1,
            skipped=0
        )
        assert result_with_error.status == SuiteStatus.ERROR

        # Any failure -> failure (if no errors)
        result_with_failure = SuiteResult(
            suite=Suite(name='test', projects=[]),
            status=SuiteStatus.FAILURE,
            duration=1.0,
            project_results=[
                ProjectResult(project=proj_ref, status=ProjectStatus.FAILURE, duration=1.0)
            ],
            total_projects=1,
            successful=0,
            failed=1,
            errors=0,
            skipped=0
        )
        assert result_with_failure.status == SuiteStatus.FAILURE
