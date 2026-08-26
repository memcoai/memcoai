Clients
=======

A client owns one gRPC connection, the credential sent on every call, and the
health check performed when it is opened. The operations themselves live on
namespaces hanging off it.

.. autoclass:: memco.client.Client
   :members:

.. autoclass:: memco.client.AsyncClient
   :members:

Provenance
----------

.. autofunction:: memco.client.provenance
