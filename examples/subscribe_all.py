"""
Subscribe to any change happening in the Ui24R
"""
import asyncio

from ui24r_client import Ui24RClient

__all__ = ["main"]


async def main() -> None:
    async with Ui24RClient.create("10.29.0.99") as client:
        channel = client.input(17)

        async with client.subscribe_all() as iterator:
            async for key, value in iterator:
                print(key, value)


asyncio.run(main())
