"""
Example of showing the VU meter for a channel in real time.
"""
import asyncio

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(17)

        async with channel.vu_meter.subscribe() as iterator:
            async for value in iterator:
                print("vu meter status =", value)


asyncio.run(main())
