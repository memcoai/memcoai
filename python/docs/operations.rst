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

Returned by ``client.memory.with_session(...)``, which opens a session and
applies it to every call made through the result.

.. autoclass:: memco.operations.SessionScope
   :members:

.. autoclass:: memco.operations.AsyncSessionScope
   :members:

.. autoclass:: memco.operations.AsyncSessionOpener
