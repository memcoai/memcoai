Administration
==============

Network and user administration, reached as ``client.networks`` and
``client.users`` on a client built with an API client's ``client_id`` and
``client_secret``. Every call acts on that API client's own organization, and is
bounded by the scopes it was granted.

The examples here are fragments: they take ``client`` as already bound, and
every type they return is from :mod:`memcoai.types`.

Networks
--------

Reached as ``client.networks``.

.. autoclass:: memcoai.administration.NetworkOperations
   :members:

.. autoclass:: memcoai.administration.AsyncNetworkOperations
   :members:

Users
-----

Reached as ``client.users``.

.. autoclass:: memcoai.administration.UserOperations
   :members:

.. autoclass:: memcoai.administration.AsyncUserOperations
   :members:
