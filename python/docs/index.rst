memco
=====

Python SDK for `Memco Shared Memory <https://memco.ai>`_.

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. An agent searches it before starting work and writes back what it
learned when it finishes, so what one agent establishes, every teammate's agent
can find.

You need an account and an API key to use this SDK. Create one at
`memco.ai <https://memco.ai>`_.

.. code-block:: bash

   pip install memco

.. code-block:: python

   from memco import Memco
   with Memco() as client:                 # reads MEMCO_API_TOKEN
       session = client.memory.start_session("coding")
       result = client.memory.search(
           "how should a client authenticate against the memory API",
           session_id=session.session_id,
       )
       for memory in result.memories:
           for insight in memory.insights:
               print(insight.title, insight.updated)

The memory operations live on :attr:`~memco.Memco.memory`.

Every failure is a subclass of :class:`~memco.errors.MemcoError`, so no raw
``grpc.RpcError`` ever reaches a caller.

See more at `docs.memco.ai <https://docs.memco.ai>`_.

.. toctree::
   :maxdepth: 2
   :caption: Reference

   clients
   operations
   types
   errors
