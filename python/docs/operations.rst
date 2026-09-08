Operations
==========

Every memory operation, reached either on the client or on a session scope that
applies one session to each of them.

The examples here are fragments: they take ``client`` and ``session`` as
already bound. ``Memco`` and ``AsyncMemco`` are imported from ``memco``, and
every other type they name — :class:`~memco.types.Tag`,
:class:`~memco.types.FeedbackRating`, :class:`~memco.types.ImportedMemory` and
the rest — from :mod:`memco.types`.

Memory
------

Reached as ``client.memory``.

.. autoclass:: memco.operations.MemoryOperations
   :members:

.. autoclass:: memco.operations.AsyncMemoryOperations
   :members:

Sessions
--------

Returned by ``client.memory.start_session(...)`` and
``client.memory.with_session(...)`` alike; both hand back the same object,
with every session-bound operation already applied.

.. autoclass:: memco.operations.Session
   :members:

.. autoclass:: memco.operations.AsyncSession
   :members:

.. autoclass:: memco.operations.AsyncSessionOpener
