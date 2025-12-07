"""Tests for GBS Suite Loader

Tests suite YAML parsing, validation, and dependency checking.
"""

import pytest
from pathlib import Path
import tempfile
import yaml

from gbs.suite import load_suite, LoadError
from gbs.suite.model import Suite, SuiteSettings, ProjectReference


@pytest.fixture
def temp_suite_dir(tmp_path):
    """Create a temporary directory for suite files"""
    return tmp_path


def write_suite_file(path: Path, content: dict):
    """Helper to write suite YAML file"""
    with open(path, 'w') as f:
        yaml.dump(content, f)


class TestBasicLoading:
    """Test basic suite file loading"""

    def test_minimal_suite(self, temp_suite_dir):
        """Test loading a minimal valid suite"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        assert suite.name == 'test-suite'
        assert suite.description is None
        assert len(suite.projects) == 1
        assert suite.settings.max_parallel_projects == 4  # default

    def test_suite_with_description(self, temp_suite_dir):
        """Test suite with description field"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'description': 'Test suite description',
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        assert suite.description == 'Test suite description'

    def test_suite_with_settings(self, temp_suite_dir):
        """Test suite with custom settings"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'settings': {
                'max_parallel_projects': 8,
                'stop_on_failure': True
            },
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        assert suite.settings.max_parallel_projects == 8
        assert suite.settings.stop_on_failure is True

    def test_missing_name_field(self, temp_suite_dir):
        """Test error when name field is missing"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        with pytest.raises(LoadError, match="Suite must have a 'name' field"):
            load_suite(suite_file)

    def test_missing_projects_field(self, temp_suite_dir):
        """Test error when projects field is missing"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite'
        })

        with pytest.raises(LoadError, match="Suite must have at least one project"):
            load_suite(suite_file)

    def test_empty_projects_list(self, temp_suite_dir):
        """Test error when projects list is empty"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': []
        })

        with pytest.raises(LoadError, match="Suite must have at least one project"):
            load_suite(suite_file)

    def test_nonexistent_file(self, temp_suite_dir):
        """Test error when suite file doesn't exist"""
        suite_file = temp_suite_dir / "nonexistent.gbs.yaml"

        with pytest.raises(LoadError, match="Suite file not found"):
            load_suite(suite_file)


class TestProjectReferences:
    """Test project reference parsing"""

    def test_simple_project(self, temp_suite_dir):
        """Test loading a simple project reference"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'project1',
                    'path': 'projects/project1'
                }
            ]
        })

        suite = load_suite(suite_file)
        assert len(suite.projects) == 1
        proj = suite.projects[0]
        assert proj.name == 'project1'
        assert proj.path == temp_suite_dir / 'projects' / 'project1'
        assert proj.output_groups is None
        assert proj.depends_on == []
        assert proj.tags == []
        assert proj.skip is False

    def test_project_with_all_fields(self, temp_suite_dir):
        """Test project with all optional fields"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'project1',
                    'path': 'projects/project1',
                    'output_groups': ['build', 'test'],
                    'max_parallel': 2,
                    'depends_on': [],
                    'tags': ['fpga', 'simulation'],
                    'skip': True
                }
            ]
        })

        suite = load_suite(suite_file)
        proj = suite.projects[0]
        assert proj.output_groups == ['build', 'test']
        assert proj.max_parallel == 2
        assert proj.tags == ['fpga', 'simulation']
        assert proj.skip is True

    def test_absolute_path(self, temp_suite_dir):
        """Test project with absolute path"""
        abs_path = temp_suite_dir / 'projects' / 'project1'
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'project1',
                    'path': str(abs_path)
                }
            ]
        })

        suite = load_suite(suite_file)
        proj = suite.projects[0]
        assert proj.path == abs_path

    def test_missing_project_name(self, temp_suite_dir):
        """Test error when project name is missing"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'path': 'projects/project1'
                }
            ]
        })

        with pytest.raises(LoadError, match="Project must have a 'name' field"):
            load_suite(suite_file)

    def test_missing_project_path(self, temp_suite_dir):
        """Test error when project path is missing"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'project1'
                }
            ]
        })

        with pytest.raises(LoadError, match="Project 'project1' must have a 'path' field"):
            load_suite(suite_file)


class TestDependencies:
    """Test dependency validation"""

    def test_simple_dependency(self, temp_suite_dir):
        """Test simple dependency chain"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'lib',
                    'path': 'lib'
                },
                {
                    'name': 'app',
                    'path': 'app',
                    'depends_on': ['lib']
                }
            ]
        })

        suite = load_suite(suite_file)
        assert suite.projects[1].depends_on == ['lib']

    def test_missing_dependency(self, temp_suite_dir):
        """Test error when dependency doesn't exist"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'app',
                    'path': 'app',
                    'depends_on': ['nonexistent']
                }
            ]
        })

        with pytest.raises(LoadError, match="Project 'app' depends on unknown project 'nonexistent'"):
            load_suite(suite_file)

    def test_circular_dependency_simple(self, temp_suite_dir):
        """Test detection of simple circular dependency"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'a',
                    'path': 'a',
                    'depends_on': ['b']
                },
                {
                    'name': 'b',
                    'path': 'b',
                    'depends_on': ['a']
                }
            ]
        })

        with pytest.raises(LoadError, match="Circular dependency detected"):
            load_suite(suite_file)

    def test_circular_dependency_complex(self, temp_suite_dir):
        """Test detection of complex circular dependency"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'a',
                    'path': 'a',
                    'depends_on': ['b']
                },
                {
                    'name': 'b',
                    'path': 'b',
                    'depends_on': ['c']
                },
                {
                    'name': 'c',
                    'path': 'c',
                    'depends_on': ['a']
                }
            ]
        })

        with pytest.raises(LoadError, match="Circular dependency detected"):
            load_suite(suite_file)

    def test_self_dependency(self, temp_suite_dir):
        """Test detection of self-dependency"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'a',
                    'path': 'a',
                    'depends_on': ['a']
                }
            ]
        })

        with pytest.raises(LoadError, match="Circular dependency detected"):
            load_suite(suite_file)

    def test_complex_dependency_graph(self, temp_suite_dir):
        """Test valid complex dependency graph"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'projects': [
                {
                    'name': 'utils',
                    'path': 'utils'
                },
                {
                    'name': 'core',
                    'path': 'core',
                    'depends_on': ['utils']
                },
                {
                    'name': 'app1',
                    'path': 'app1',
                    'depends_on': ['core']
                },
                {
                    'name': 'app2',
                    'path': 'app2',
                    'depends_on': ['core', 'utils']
                }
            ]
        })

        suite = load_suite(suite_file)
        assert len(suite.projects) == 4
        # Verify dependencies are correctly loaded
        assert suite.projects[1].depends_on == ['utils']
        assert suite.projects[2].depends_on == ['core']
        assert suite.projects[3].depends_on == ['core', 'utils']


class TestSettings:
    """Test suite settings parsing and validation"""

    def test_max_parallel_validation(self, temp_suite_dir):
        """Test validation of max_parallel_projects defaults to 4 when invalid"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'settings': {
                'max_parallel_projects': 0
            },
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        # Invalid value should default to 4
        assert suite.settings.max_parallel_projects == 4

    def test_negative_max_parallel(self, temp_suite_dir):
        """Test negative max_parallel_projects defaults to 4"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'settings': {
                'max_parallel_projects': -1
            },
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        # Negative value should default to 4
        assert suite.settings.max_parallel_projects == 4

    def test_output_settings(self, temp_suite_dir):
        """Test output settings"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'settings': {
                'output': {
                    'junit_xml': 'results/junit.xml',
                    'summary_json': 'results/summary.json',
                    'log_dir': 'logs',
                    'save_logs': False,
                    'tail_lines': 50
                }
            },
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        output = suite.settings.output
        assert output.junit_xml == temp_suite_dir / 'results' / 'junit.xml'
        assert output.summary_json == temp_suite_dir / 'results' / 'summary.json'
        assert output.log_dir == temp_suite_dir / 'logs'
        assert output.save_logs is False
        assert output.tail_lines == 50

    def test_filter_settings(self, temp_suite_dir):
        """Test filter settings"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        write_suite_file(suite_file, {
            'name': 'test-suite',
            'settings': {
                'filter': {
                    'enabled': True,
                    'base_commit': 'main',
                    'target_commit': 'HEAD'
                }
            },
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        })

        suite = load_suite(suite_file)
        filter_settings = suite.settings.filter
        assert filter_settings.enabled is True
        assert filter_settings.base_commit == 'main'
        assert filter_settings.target_commit == 'HEAD'


class TestRawConfig:
    """Test that raw config is preserved"""

    def test_raw_config_preserved(self, temp_suite_dir):
        """Test that raw YAML config is stored"""
        suite_file = temp_suite_dir / "suite.gbs.yaml"
        config = {
            'name': 'test-suite',
            'description': 'Test description',
            'custom_field': 'custom_value',
            'projects': [
                {'name': 'test-project', 'path': 'test'}
            ]
        }
        write_suite_file(suite_file, config)

        suite = load_suite(suite_file)
        assert suite.raw_config['name'] == 'test-suite'
        assert suite.raw_config['description'] == 'Test description'
        assert suite.raw_config['custom_field'] == 'custom_value'
