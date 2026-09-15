"""Tests for the ISE backend, chiefly the per-project step options"""

import pytest

from gbs.base import BaseBackend
from gbs.builtin.ise.backend import IseBackend
from gbs.builtin.ise.passes import IseSynthesizePass
from gbs.builtin.ise.task import Map, Par, flags


SPARTAN6 = {"target": {"part": "xc6slx9-2tqg144"}}


def test_backend_creation():
    backend = IseBackend()

    assert isinstance(backend, BaseBackend)
    assert backend.name == "gbs.builtin.ise"


def test_contribute_passes_with_bitstream_output():
    backend = IseBackend()

    passes = backend.contribute_passes(SPARTAN6, {"ise-bitstream"})

    assert len(passes) == 1
    assert isinstance(passes[0], IseSynthesizePass)


def test_contribute_passes_refuses_a_post_ise_part():
    """A 7-series part is not ISE's to build."""
    backend = IseBackend()

    passes = backend.contribute_passes(
        {"target": {"part": "xc7a35t-1csg324"}}, {"ise-bitstream"})

    assert all(p.probe() is not None for p in passes)


def test_flags_default_to_the_step_defaults():
    assert flags({"ol": "high", "xe": "c"}, {}) == ["-ol", "high", "-xe", "c"]


def test_flags_take_an_override():
    """An override replaces its own default and leaves the rest alone."""
    got = flags({"ol": "high", "xe": "c"}, {"ol": "std"})

    assert got == ["-ol", "std", "-xe", "c"]


def test_flags_add_an_option_the_defaults_do_not_carry():
    got = flags({"ol": "high"}, {"pr": "b"})

    assert got == ["-ol", "high", "-pr", "b"]


def test_flags_drop_a_default_on_none():
    """None is how a project turns off something the defaults ask for."""
    got = flags({"ol": "high", "retiming": "on"}, {"retiming": None})

    assert got == ["-ol", "high"]


def test_flags_stringify_values():
    assert flags({}, {"mt": 2}) == ["-mt", "2"]


def test_map_carries_the_flags_ise_needs_by_default():
    """The defaults are what the backend shipped before they were exposed."""
    assert flags(Map.default_options, {}) == [
        "-ol", "high",
        "-xe", "c",
        "-mt", "on",
        "-global_opt", "speed",
        "-retiming", "on",
        "-register_duplication", "on",
        "-equivalent_register_removal", "off",
        "-lc", "area",
    ]


def test_par_carries_the_flags_ise_needs_by_default():
    assert flags(Par.default_options, {}) == ["-ol", "high", "-xe", "c"]


def test_map_options_reach_the_command_line():
    """The IOB packing case this was added for."""
    got = flags(Map.default_options, {"pr": "b"})

    assert got[-2:] == ["-pr", "b"]
    assert "-global_opt" in got


def test_pass_forwards_step_options_to_its_dispatcher(tmp_path):
    config = dict(SPARTAN6, map_options={"pr": "b"}, par_options={"ol": "std"})
    synth = IseSynthesizePass(config, None, None)

    assert synth.config.get("map_options") == {"pr": "b"}
    assert synth.config.get("par_options") == {"ol": "std"}
