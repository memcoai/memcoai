memcoai
=======

Python SDK for `Memco Shared Memory <https://memco.ai>`_.

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. An agent searches it before starting work and writes back what it
learned when it finishes, so what one agent establishes, every teammate's agent
can find.

You need an account and an API key to use this SDK. Create one at
`memco.ai <https://memco.ai>`_.

.. code-block:: bash

   pip install memcoai

.. code-block:: python

   from memcoai import Memco
   with Memco() as client:                 # reads MEMCO_API_TOKEN
       session = client.memory.start_session("coding")
       result = session.search("how should a client authenticate against the memory API")
       for memory in result.memories:
           for insight in memory.insights:
               print(insight.title, insight.updated)

The memory operations live on :attr:`~memcoai.Memco.memory`.

Every failure is a subclass of :class:`~memcoai.errors.MemcoError`, so no raw
``grpc.RpcError`` ever reaches a caller.

See more at `docs.memco.ai <https://docs.memco.ai>`_.

.. toctree::
   :maxdepth: 2
   :caption: Reference

   clients
   operations
   agent
   types
   errors
