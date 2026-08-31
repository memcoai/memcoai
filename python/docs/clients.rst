Clients
=======

A client owns one gRPC connection, the credential sent on every call, and the
checks performed when it is opened — a health probe, and the ``list_domains``
call that proves the credential and reports the service's input limits. The
operations themselves live on namespaces hanging off it.

.. autoclass:: memco.Memco
   :members:

.. autoclass:: memco.AsyncMemco
   :members:

Provenance
----------

.. autofunction:: memco.provenance
