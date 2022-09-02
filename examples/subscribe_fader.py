"""
Subscribe to fader changes for a certain channel.
"""
import asyncio

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(17)

        async with channel.fader.subscribe() as iterator:
            async for value in iterator:
                print("fader for channel=", value)


asyncio.run(main())
