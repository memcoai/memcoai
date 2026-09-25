Clients
=======

A client owns one gRPC connection, the credential sent on every call, and the
checks performed when it is opened — a health probe, then a call that proves the
credential. With a token, that is ``list_domains``, which also reports the
service's input limits. With an API client's ``client_id`` and
``client_secret``, it is the exchange of those for a token, which the client
renews by itself before it expires. The operations themselves live on
namespaces hanging off it: ``client.memory``, and ``client.networks`` and
``client.users`` for a client with client credentials.

Closing a client ends the key of any session still acting for one of your users,
so a session left open does not hold a key until it expires.

.. autoclass:: memcoai.Memco
   :members:

.. autoclass:: memcoai.AsyncMemco
   :members:

Provenance
----------

.. autofunction:: memcoai.provenance
