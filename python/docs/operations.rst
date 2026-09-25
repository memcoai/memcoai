Operations
==========

Every memory operation, reached either on the client or on a session scope that
applies one session to each of them.

The examples here are fragments: they take ``client`` and ``session`` as
already bound. ``Memco`` and ``AsyncMemco`` are imported from ``memcoai``, and
every other type they name — :class:`~memcoai.types.Tag`,
:class:`~memcoai.types.FeedbackRating`, :class:`~memcoai.types.ImportedMemory` and
the rest — from :mod:`memcoai.types`.

Memory
------

Reached as ``client.memory``.

.. autoclass:: memcoai.operations.MemoryOperations
   :members:

.. autoclass:: memcoai.operations.AsyncMemoryOperations
   :members:

Sessions
--------

Returned by ``client.memory.start_session(...)`` and
``client.memory.with_session(...)`` alike; both hand back the same object,
with every session-bound operation already applied.

Given an ``external_id``, either opens a session acting as that external user,
on a client built with an API client's credentials. The client mints an
impersonation key for the user, lists the domains under it, so the session
learns the limits that apply to the user, and then starts the session. Every
call through the session carries that key, which renews itself before it
expires. Close the session when done, by leaving its ``with`` block or calling
``close()``. That ends the key at once, and the service caps how many live keys
each user may hold. One dropped without being closed has its key ended only
eventually, at the client's next call once it is garbage-collected, with a
``ResourceWarning``. A session opened without an ``external_id`` holds nothing,
and closing it changes nothing.

.. autoclass:: memcoai.operations.Session
   :members:

.. autoclass:: memcoai.operations.AsyncSession
   :members:

.. autoclass:: memcoai.operations.AsyncSessionOpener
