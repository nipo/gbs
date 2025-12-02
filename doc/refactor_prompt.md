I'd like to do a refactoring of the build system.

Today, we have the exploration phase where we walk down repositories
from the root partition. We get a bunch of source files to compile.
We have backends that can match those sources to create an
output. This output is loosely defined.

I'd like we change two things:

* define output in the project definition, for instance for a
  simulation project it could be

```yaml
output:
  - type: simulator
    path: simulation.exe
```

or for a synthesis backend:

```yaml
output:
  - type: gowin-bitstream
    path: somehting.fs
```

That would allow us to have multiple outputs defined. That wouls also
lighten the need for profiles. We could have all backends defined all
the time. That would also totally change the way we create the
BuildFileSet.

We would have a phase of design space exploration where GBS would need
to find a way to "connect" source tree with 
