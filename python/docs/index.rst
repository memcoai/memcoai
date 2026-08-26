memco
=====

Python SDK for Memco Shared Memory — a persistent store your team and its
agents share.

.. code-block:: python

   from memco.client import Client

   with Client() as client:                 # reads MEMCO_API_TOKEN
       session = client.memory.start_session("coding")
       result = client.memory.search(
           "how should a client authenticate against the memory API",
           session_id=session.session_id,
       )
       for memory in result.memories:
           for insight in memory.insights:
               print(insight.title, insight.updated)

The memory operations live on :attr:`~memco.client.Client.memory`.

Every failure is a subclass of :class:`~memco.client.MemcoError`, so no raw
``grpc.RpcError`` ever reaches a caller.

.. toctree::
   :maxdepth: 2
   :caption: Reference

   clients
   operations
   types
   errors
