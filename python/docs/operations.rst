Operations
==========

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
