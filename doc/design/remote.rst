Remote Execution
================

Remote execution runs some passes of a build on a gbs instance on
another host, reached over ssh. The local instance keeps configuration
loading, repository resolution, planning and up-to-date checks; the
remote one runs the passes whose tools it has. See :doc:`../remote`
for the user guide.

The code lives in ``gbs.remote``; this page describes its structure
and the reasons behind it.

Constraints
-----------

The hosts share nothing: no filesystem, no user, no directory layout,
not even the operating system on the local side. Remote hosts run
Linux. Both run the same gbs.

The build system imposes the rest:

- **Tasks are not serializable.** A ``BuildStep`` is an
  ``asyncio.Future`` holding live references to its dispatcher,
  context and tool session, and reads dispatcher state when it runs.
- **Tool sessions are stateful.** Diamond, Vivado, Yosys or Gowin
  sessions carry state from one task to the next. The smallest unit
  that can move is every task sharing a session, in practice the
  passes of a backend.
- **Tasks bake absolute paths.** ``Task.work()`` writes host paths
  into command lines, Tcl scripts, ``.qsf``, ``.ldf`` or project
  files.
- **Planning probes tools.** Pass constructors and ``probe()`` look for
  tools, query versions and part databases. A tool absent locally
  makes the planner reject its pass.

Design Choice
-------------

Several shapes were considered:

**Per-task archive and one-shot remote run**
    Ship each task with its inputs to ``ssh host gbs run-task``. Tasks
    are not serializable, sessions span tasks, and paths are already
    baked by the time a task is known.

**Transparent process remoting**
    Run the tool command lines or sessions over ssh, with sshfs or
    identical mounts. Needs identical paths on both hosts, or path
    rewriting in every backend.

**Whole-project remote build**
    The remote needs every repository, and the local host loses its
    planning and up-to-date checks. Too coarse.

**Files served on demand**
    Let the remote ask for files as tools open them. Tools read
    through the operating system, so this needs FUSE on the remote.

**Segment re-dispatch** (chosen)
    Send the *plan* of a group of passes, not tasks. The remote
    recreates the passes from its own backends, dispatches them on its
    own paths, and runs the resulting tasks itself. Backends are
    unaware of it: no path rewriting, sessions stay whole, and pass
    construction and probes run where the tool is.

Components
----------

.. code-block:: text

   local gbs                                     remote gbs
   ─────────                                     ──────────
   BuildPlanner ── ToolHost ───────────────┐
     LocalToolHost                         │
     RemoteToolHost ── passes.contribute ──┼──▶ RemoteServer ── backends
                                           │       │
   PlanRealization                         │       ▼
     RemoteSegmentDispatcher ─ segment.dispatch ─▶ SegmentRun (dispatch)
     RemoteSegmentTask ─────── blob.*  ────┼──▶ BlobStore
                         ─── segment.execute ─▶ SegmentRun (build)
                         ◀── message/progress events
     OutputInstaller ◀──────── blob.get ───┘

=====================  ==================================================
Module                 Role
=====================  ==================================================
``wire``               Strict JSON readers, descriptor format version
``roots``              Root table, host-independent paths
``resource``           Resource descriptors and metadata codec
``manifest``           Content manifests and the blob store
``segment``            Pass, output group and segment descriptors
``channel``            Framing over a byte stream
``peer``               Multiplexed requests, events and cancellation
``handshake``          Identities, source digests, plugin compatibility
``server``             Serving side, ``gbs remote serve --stdio``
``client``             Connection to a remote host over ssh
``toolhost``           Hosts tools run on, planning queries
``planning``           Pass contributions and their local stand-in
``transfer``           Blob upload and download in pieces
``segment_run``        Serving side of segment dispatch and execution
``execution``          Plan segmentation, segment dispatcher and task
=====================  ==================================================

Paths and Roots
---------------

A path never crosses the wire as it is. It is a ``RootedPath``: a root
id and a normalized relative POSIX path. A ``RootTable`` lists the
roots of a build (``RootTable.from_realization()``):

- ``project``: the project directory;
- ``repo-<n>``: each repository root;
- ``output``: the base build output directory;
- ``cache``: the shared cache root;
- ``extra-<n>``: the outermost directory of any file the build covers
  that lies outside all of the above (repositories may reference
  sibling directories, or libraries up the tree).

A root lying inside another is recorded as a location in it. The
remote places outermost roots at ``<segment dir>/roots/<id>/`` and
nested roots at the same relative location inside their parent, so
relative references between files (``include`` directives, relative
paths in project files) keep working whichever root each file is
expressed against.

GBS configuration files are not covered: they describe the local
host's tools, and the remote uses its own.

Descriptors
-----------

Descriptors are JSON documents read strictly (``WireObject``): a
missing field, an unknown field or a value of the wrong type is an
error, not a default, so a mismatch surfaces where it happens instead
of as a wrong build. Top-level documents carry ``WireFormat.VERSION``,
which also serves as the protocol version. Free-form values (backend
configuration, filter variables, project configuration) must survive a
JSON round trip unchanged, or are refused.

Resources
~~~~~~~~~

A ``ResourceDescriptor`` holds a resource's location, file type and
aliases, type version, library, typology, producer name, whether it is
a directory, and its metadata.

Resource metadata is free-form, and nothing tells a path from another
string. ``ResourceMetadataCodec`` therefore declares the kind of every
key that may cross hosts: ``string``, or ``directory-list``, whose
entries are translated through the root table and whose trees are
content the resource depends on. An undeclared key is an error; plugins
attaching their own metadata declare it with
``ResourceMetadataCodec.register()``.

Passes and Segments
~~~~~~~~~~~~~~~~~~~

A ``PassDescriptor`` identifies a planned pass by what recreates it:
backend name, pass name, pass class, backend configuration (target and
tool overrides included) and requested output types. Pass constructors
derive their state from these, the project configuration and the gbs
configuration, so asking the backend again on the other host yields an
equivalent pass, except for the gbs configuration, which is that
host's own. ``instantiate()`` refuses a backend returning no pass or
several of that name, a pass of another class, or a pass its
``probe()`` rejects.

An ``OutputGroupDescriptor`` carries the output group settings
dispatchers read: name, top cell and its library, target, backend
configuration, excluded dispatchers, and output locations.

A ``SegmentDescriptor`` carries everything needed to rebuild a plan for
a subset of passes:

- project name and raw configuration, output group, filter variables;
- the passes, in plan order;
- the root table, base output and shared cache locations;
- ``inputs``: resources the segment starts from, in pending queue
  order, and ``dependencies``, the indices of the inputs each depends
  on (partition dependencies of sources);
- ``goals``: resources the segment must produce;
- ``exported_types``: types of produced resources other passes
  consume;
- ``generic_plugins``: plugins whose generic dispatchers run in the
  segment;
- ``manifest``: content of the inputs present when the segment is
  described.

The remote builds a ``BuildPlan`` from it with no repositories: sources
come as resolved inputs, not as repositories to load.

Content and Blobs
-----------------

A ``ContentManifest`` lists the files and directories a set of
resources consists of, keyed by location: each file with its SHA-256,
size and executable bit, each directory of a directory tree (so empty
ones are rebuilt too). Overlapping resources yield one entry per
location. Symbolic links are followed and listed as what they point
to, since the other host may not hold the target; dangling links and
directory loops are errors.

A ``BlobStore`` is content-addressed: blob ``h`` is
``<root>/<h[:2]>/<h>``, stored read-only. A blob enters the store only
complete and matching its hash, through a temporary file renamed into
place. The remote store defaults to
``$XDG_CACHE_HOME/gbs/remote-blobs`` and persists across connections,
so unchanged content is sent once. ``ContentManifest.materialize()``
rebuilds a tree from a store; segment inputs are copied, not
hard-linked, so a tool writing to an input in place cannot alter the
store.

Transfers go through ``BlobTransfer``: ``blob.have`` finds what the
other side lacks, then blobs travel in 16 MiB pieces, up to 8 blobs at
a time, each piece one request, so no frame bound limits blob size.

Input Completeness
~~~~~~~~~~~~~~~~~~

The remote only has what it is sent, so every file a tool reads must be
declared by a resource. Two mechanisms cover what single files do not:

- **Directory resources.** A ``Resource`` created with
  ``directory=True`` stands for a whole tree (library directories,
  generated IP trees, tool work directories). Its manifest lists every
  file below it. Locally, it exists when the directory does and its
  modification time is the newest in the tree, so any change below it
  makes its consumers out of date.
- **Directory-list metadata**, such as ``include_dirs`` on sources,
  sends the listed trees along with the resource.

A file no resource declares is missing remotely, and the tool fails
there rather than silently reading a shared filesystem.

Channel
-------

Framing
~~~~~~~

``FrameChannel`` exchanges frames over an asyncio stream pair:

.. code-block:: text

   u32 BE header length | header (UTF-8 JSON object)
   u32 BE body length   | body (opaque bytes)

Headers are bounded to 16 MiB and bodies to 256 MiB; a frame exceeding
a bound is refused before any of it is sent or buffered. File content
travels in bodies as is, not encoded in JSON. Writes are serialized so
concurrent writers never interleave.

``gbs remote serve --stdio`` duplicates its standard input and output
for the channel, then points descriptor 1 to standard error and
descriptor 0 to ``/dev/null``: neither Python code nor tool
subprocesses can corrupt or consume the channel. Standard error is the
remote log; the client forwards it to its own log and quotes its last
lines in connection failures.

Messages
~~~~~~~~

Both ends are ``Peer`` objects: either may issue requests, several in
flight in each direction, served by handlers registered by method name,
each in its own task. Frame headers are one of:

.. code-block:: text

   {kind: request,  id, method, params}
   {kind: response, id, result}
   {kind: response, id, error: {type, message, data?}}
   {kind: event,    name, data, request?}
   {kind: cancel,   id}

Request ids are chosen by the requester. An event carrying ``request``
comes from the handler serving that request, and reaches the
requester's callback for it before the response. Cancelling the
requesting coroutine sends ``cancel``, which cancels the handler on
the other side; this is how interrupting a local build stops the
remote one. When the connection ends, pending requests fail with
``ChannelClosed`` and running handlers are cancelled.

Handshake
---------

The client's first request is ``hello``, carrying its ``Identity`` and
whether sources must match. An identity holds the protocol version,
the gbs version, each plugin's version, and source digests of gbs and
of each plugin.

A source digest covers a module's own tree only: the directory of a
regular package (even when its ``__path__`` is extended by other
installs, as plugins do for ``gbs``), every entry of a namespace
package's ``__path__``, or a plain module's file. The file map gives
the SHA-256 of each ``.py`` file outside ``__pycache__``; the digest
covers the map.

The server answers with its identity and its tool inventory: every
declared tool as name, variant, version and the reason it is unusable
there, if any. Tool paths and environments never leave their host.

Protocol and gbs versions must match, and gbs sources must too unless
the client opts out for the host (``check_sources: false``). On a
source mismatch, the client fetches the remote file map with
``sources.files`` and lists the files that differ. Both sides check:
the server refuses every method but ``sources.files`` and
``shutdown`` from a client it does not accept.

Plugin differences do not prevent the connection; see
`Plugin Compatibility`_.

Methods
~~~~~~~

Served by ``RemoteServer``:

``hello``
    ``{identity, check_sources}`` → ``{identity, tools: [{name,
    variant, version, problem}]}``. Required first.

``sources.files``
    ``{key}`` → ``{files: {path: sha256}}``. File map under a source
    key, ``gbs`` or a plugin name. Allowed after a refused identity.

``blob.have``
    ``{digests}`` → ``{missing}``.

``blob.put``
    ``{digest, offset, size}`` with a piece as body → ``{}``. Pieces
    come in order from offset 0; the blob enters the store with the
    last one, if its hash matches.

``blob.get``
    ``{digest, offset, size}`` → ``{size}`` with up to ``size`` bytes
    from ``offset`` as body; the result is the blob size.

``passes.contribute``
    ``{backend, config, requested_types, project_config}`` →
    ``{passes: [contribution]}``. A contribution is ``{pass, input_types,
    output_types, types_with_library, can_fork, priority, filter_vars,
    problem}``, ``pass`` being a pass descriptor, ``filter_vars`` set
    exactly when ``problem`` is null. Refused with
    ``IncompatiblePlugin`` for a backend of a plugin not identical on
    both hosts.

``segment.dispatch``
    Segment descriptor → ``{id, goals, exported, pending_inputs}``.
    Dispatches the segment and describes what it produces.

``segment.execute``
    ``{id, manifest}`` → ``{manifest}``. Builds the segment from the
    given inputs and answers with the content of its outputs, whose
    blobs are then in the store. Emits ``message`` and ``progress``
    events. A failed build answers ``BuildFailed`` with ``{headline,
    report}`` as data.

``shutdown``
    ``{}``, then the server closes the connection.

Error types include ``HandshakeRequired``, ``Incompatible``,
``ProtocolError``, ``UnknownBackend``, ``IncompatiblePlugin``,
``MissingBlob``, ``UnknownSegment``, ``Unproduced``, ``BuildFailed``,
``Cancelled`` and ``UnknownMethod``.

Planning Through Tool Hosts
---------------------------

A ``ToolHost`` answers which tools a host declares, and which passes a
backend contributes there:

- ``LocalToolHost`` calls ``backend.contribute_passes()`` with the
  local configuration and probes each pass.
- ``RemoteToolHost`` sends the query as ``passes.contribute``. The
  remote constructs and probes the passes with its own configuration
  and answers with their planning interface. Each is represented
  locally by a ``RemotePass``, carrying its host and descriptor.

Probes never cross the wire one by one: the whole backend query is
delegated, so pass constructors reading part databases or tool
versions run where the tool is. The query is a round trip, so the
planner is asynchronous (``await planner.plan(output_group)``).
Answers are cached per query for the connection's lifetime, as the
backwards search repeats them.

``BuildPlanner`` takes the tool hosts in order of preference and asks
every backend on each. A pass is taken from the first host accepting
it: with ``--remote``, the remote host comes first, so it wins over
the local host for every pass it accepts. A backend a host cannot
serve raises ``BackendUnavailable``; the reason, like every probe
rejection, is recorded per host and listed in the plan-failure
diagnostic.

``RemotePass.output_path()`` keeps the declared output path, and
``dispatchers()`` must never be called: the segment dispatches remote
passes.

Segmentation
------------

``PlanSegments`` groups remote passes by host. Passes are linked by
data flow: a pass feeds another when one of its output types (with
terminal-type aliases) is an input type of the other. A segment runs
as a whole, so no data flow path may leave a segment and come back to
it.

Each pass gets a level: the largest number of times a data flow path
reaching it leaves a remote pass for a pass on another host. Remote
passes of one host at one level form a segment. A path leaving a
segment raises the level of everything after it, so it never re-enters
that segment. Each segment records its upstream segments. A data flow
loop between passes is a configuration error.

``PlanRealization`` registers, for each segment, one
``RemoteSegmentDispatcher`` in place of the dispatchers of its passes.

Settled Dispatch
----------------

A segment can only be described once every resource it consumes is
in the pending queue, including those produced by local passes and
by other segments. Dispatchers normally act in ``process()`` rounds
repeated until the queue stops changing. ``BaseDispatcher`` adds a
phase for this case:

.. code-block:: python

   async def process_settled(self) -> None:
       """Called when a whole round of process() calls left the
       pending queue unchanged"""

When a round changes nothing, ``BuildContext.run_dispatcher_iteration()``
calls ``process_settled()`` on each dispatcher in registration order
until one changes the queue; ``process()`` rounds then resume.

``RemoteSegmentDispatcher.process_settled()`` acts once its upstream
segments are dispatched. It:

1. checks the plugins of the segment passes are identical on the
   host, and selects the generic dispatcher plugins that are (the
   others are warned about);
2. claims the pending resources of the segment input types, the
   ``DEFINITION`` resources inside the root table (except local GBS
   configuration files and generated ones such as the config
   fingerprint, which stay local dependencies), and the output goals of
   its output types no other pass produces;
3. uploads the content of the inputs that already exist, and sends
   ``segment.dispatch``;
4. creates one ``RemoteSegmentTask`` with the claimed resources as
   inputs, and the goals and exported resources the remote announced
   as outputs.

Inputs the remote dispatchers left pending (``pending_inputs``) are
added as non-consuming inputs, so local dispatchers still see them.
An input of the segment appearing after dispatch is an error.

On the remote, ``SegmentRun.dispatch()`` recreates the passes, builds
a ``SegmentBuildContext`` over the roots placed in
``<workspace>/segment-<id>/``, materializes the inputs sent along (so
dispatchers find files they read at dispatch time), queues inputs and
goals, registers the pass dispatchers and the listed generic
dispatchers, and runs dispatch to convergence. Every goal must end up
with a producing task. Exported resources are those produced by a
task whose type other passes consume.

Execution
---------

``RemoteSegmentTask`` is an ordinary local task: its up-to-date check
is local, from timestamps of its inputs and outputs, and a segment
that is up to date is not run. Its ``work()``:

1. computes the manifest of all inputs, uploads missing blobs, and
   sends ``segment.execute``;
2. relays ``message`` events as tool messages, with file locations
   mapped back through the root table (paths outside every root are
   shown as ``host:path``), and ``progress`` events as its progress;
3. fetches the output blobs into a staging store beside the build
   output, and installs the outputs.

On the remote, ``SegmentRun.execute()`` brings the materialized tree to
the manifest (removing files whose content changed since dispatch),
runs the build under the server's parallelism limit
(``max_parallel`` of the remote configuration, 4 by default), and
stores the outputs in the blob store. The segment directory is removed
afterwards unless the workspace is kept. A failed build answers
``BuildFailed`` with the failure summary, re-raised locally as a
``RemoteSegmentFailure`` prefixed with the host name.

``OutputInstaller`` checks every manifest entry belongs to an expected
output and every output is present, then replaces each output at once:
a file is written beside its destination and renamed over it, a
directory is built beside its destination and swapped in.

Plugin Compatibility
--------------------

Connecting only requires the protocol, gbs version and (unless
disabled) gbs sources to match. Plugins are checked where they are
used, by ``PluginCompatibility``: a plugin is compatible when installed
on both sides with the same version and, when sources are checked, the
same source digest.

- **Planning.** ``RemoteToolHost`` queries a backend on the remote only
  if its plugin is compatible; the server refuses the others with
  ``IncompatiblePlugin``. The local host remains a fallback.
- **Segment passes.** The client refuses to dispatch a segment whose
  passes belong to an incompatible plugin, naming each and the files
  that differ; the server checks the same.
- **Generic dispatchers.** The client lists, in ``generic_plugins``,
  the plugins whose generic dispatchers it registers for the output
  group and that are compatible with the host; incompatible ones are
  skipped inside the segment with a warning, and still run locally.
  The server refuses a listed plugin it finds incompatible, and skips
  (with a log line) generic dispatchers of plugins the client does not
  list, since they do not run in the client's build either.

Skipping rather than refusing keeps builds working when a plugin
unrelated to the remote backend is missing there, at the cost of
whatever its dispatchers would have generated inside the segment,
hence the warning.

Tool Redirection
----------------

A ``tools:`` entry may carry ``remote: <host>``, a ``remote_hosts``
name or an ssh destination, instead of an install location. Such a
tool is not run locally: its host becomes a tool host of every build
that needs it.

- Locally, ``LocalToolHost`` reports a redirected tool as unusable
  (``tool '...' is provided by host ...``), so the pass probe rejects
  it; ``BasePass.probe_tool()`` also records the destination in the
  pass ``redirect`` attribute.
- The planner collects, per backend, the passes rejected with a
  redirect, obtains the ``RemoteToolHost`` of each destination through
  a ``redirect_host`` callback, and queries the backend there. A
  redirected pass is only taken from its destination: contributions of
  the same pass by other hosts, including a ``--remote`` host, are left
  out. If the destination rejects it, the local rejection stays in the
  diagnostic.
- ``Project.redirect_host()`` connects destinations on first use,
  within ``hosts_open()``, and shares the connection by host name with
  the ``--remote`` host and other redirections. A build that uses no
  redirected tool never contacts the host.
- ``Project.redirects_disable(reason)`` makes the callback raise
  ``RedirectDisabled``; the reason is appended to the rejections of
  redirected passes. ``--no-remote`` uses it (and is exclusive with
  ``--remote``), as does partition validation, which reads diagnostics
  from the local build context.
- Code that runs a tool outside a build goes through
  ``ToolConfig.local()``, which raises ``ToolRedirected`` (a
  ``ConfigError``) naming the tool and its host.
- A tool entry carrying ``remote:`` and ``path`` or ``executable`` is
  refused at configuration load.

Once planned, redirected passes are ``RemotePass`` objects like those
of ``--remote``, and segmentation, dispatch and execution are the ones
described above.

Connection Lifecycle and Logging
--------------------------------

``RemoteHost.open()`` runs ``ssh -T <ssh args> -- <command> remote
serve --stdio [--keep]``: the ``command`` string is passed to the
remote shell as is, the fixed arguments quoted. The project keeps its
connections open across planning and build (``Project.hosts_open()``),
and closes them at the end whatever the outcome: a ``shutdown``
request, then waiting for the process, terminating it if it does not
exit. Realizations holding remote segments are never cached, as they
refer to connections that end with the command.

The server creates its workspace (``mkdtemp``, prefix
``gbs-remote-``) on first use and removes it when the connection ends
unless ``--keep`` is given. ``gbs remote`` commands write no log files;
they log to standard error, which the client forwards at INFO level
prefixed with the host name.
