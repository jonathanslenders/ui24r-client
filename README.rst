Ui24R Python client
===================

Python asyncio client for the Soundcraft Ui24R digital mixer.

This client provides a Python interface to the web socket API exposed by this
mixer. The client can subscribe to settings, change them, or retrieve the
current values.

This library is somewhat experimental and not extensively tested. It's
reasonably well designed, but can contain bugs.

The performance is good enough to allows real time interaction with the mixer.


Some examples
-------------

Retrieve the current fader values for our input channels.

.. code:: python

    from ui24r_client import Ui24RClient

    async with Ui24RClient.create("10.29.0.99") as client:
        for num in range(1, 21):
            channel = client.input(num)
            print(num, await channel.fader.get())

Setting/clearing the mute flag:

.. code:: python

    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(3)

        await channel.mute.set(True)
        await sleep(1)
        await channel.mute.set(False)
        await sleep(1)

Subscribe to fader changes:

.. code:: python

    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(17)

        async with channel.fader.subscribe() as iterator:
            async for value in iterator:
                print("fader for channel=", value)


Also see the examples/ directory in this repository.
