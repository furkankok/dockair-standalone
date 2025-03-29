import asyncio
import json
import os
import sys
import requests
import websockets
import logging
import subprocess



logging.basicConfig(
    level=logging.INFO, format="{asctime} - {levelname} - {message}", style="{"
)
logger = logging.getLogger(__name__)

BASE_URL = "dockair.atlantiswebstudio.com"
WEBSOCKET_SERVER_URL = f"wss://{BASE_URL}/ws/script/"

class WebsocketManager:
    def __init__(self, connection_url):
        self.connection_url = connection_url
        self.ws = None
        self.connected = False
        self.reconnect_interval = 2
        self.max_reconnect_interval = 60

    async def connect(self):
        while True:
            try:
                self.ws = await websockets.connect(self.connection_url)
                self.connected = True
                self.reconnect_interval = 2
                logger.info("WebSocket connection established")

                await self.process_incoming_messages()

            except Exception as unexpected_error:
                self.connected = False
                logger.error(f"Unexpected error: {unexpected_error}")
                await self.attempt_reconnection()


    def download_file_from_url(self, url):
        response = requests.get(url)
        return response.content

    async def process_incoming_messages(self):
        try:
            async for message in self.ws:
                try:
                    logger.info(f"Received message: {message}")

                    data = json.loads(message)
                    if "script" not in data:
                        continue

                    script = data["script"]

                    if script["type"] == "download":
                        file_urls = script["urls"]
                        for file_url in file_urls:
                            file_content = self.download_file_from_url(file_url)
                            with open(f"{file_url.split('/')[-1]}", "wb") as file:
                                file.write(file_content)

                        file_path = "requirements.txt"
                        if os.path.exists(file_path):
                            try:
                                subprocess.run(["pip3", "install", "-r", file_path], check=True)
                                logger.info(f"Successfully installed requirements from {file_path}")
                            except Exception as install_error:
                                logger.error(f"Error installing requirements: {install_error}")
                        else:
                            logger.warning(f"Requirements file not found at {file_path}")

                        import script

                        asyncio.create_task(script.main())




                except Exception as message_processing_error:
                    logger.error(
                        f"Error processing message: {message_processing_error}"
                    )
        except Exception as disconnect_error:
            self.connected = False
            logger.warning(f"Connection lost: {disconnect_error}")

    async def attempt_reconnection(self):
        logger.info(f"Attempting to reconnect in {self.reconnect_interval} seconds...")
        await asyncio.sleep(self.reconnect_interval)
        self.reconnect_interval = min(
            self.reconnect_interval * 1.1, self.max_reconnect_interval
        )


async def main():
    logger.info("Starting Dockair Client")

    try:
        websocket_manager = WebsocketManager(WEBSOCKET_SERVER_URL)
        asyncio.create_task(websocket_manager.connect())

        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Dockair Client stopped")
    except Exception as system_error:
        logger.error(f"Error: {system_error}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())


    