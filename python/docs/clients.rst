Clients
=======

A client owns one gRPC connection, the credential sent on every call, and the
health check performed when it is opened. The operations themselves live on
namespaces hanging off it.

.. autoclass:: memco.Memco
   :members:

.. autoclass:: memco.AsyncMemco
   :members:

Provenance
----------

.. autofunction:: memco.provenance
