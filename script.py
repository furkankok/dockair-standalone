import asyncio
import docker
import json
import websockets
import logging
import sys
import time
from threading import Thread
import os
import sentry_sdk
import subprocess  # Yeni eklenen import
from docker_info import DockerInfo

event_queue = asyncio.Queue()
docker_info = None


# for testing "",
if True:
    os.environ["WEBSOCKET_SERVER_URL"] = "ws://127.0.0.1:8000/ws/client/"
    os.environ["USER_TOKEN"] = "44a6ecf1-d936-4b46-983f-a28eba487707"
    os.environ["PRODUCTION"] = "false"

if os.getenv("PRODUCTION", "true").lower() == "true":
    sentry_sdk.init(
        dsn="https://596ee9ff189981b07fbcea9848a74118@o4508028078129152.ingest.de.sentry.io/4508028082520144",
        traces_sample_rate=1.0
    )

# Loglama konfigürasyonu
# logging.basicConfig(level=logging.DEBUG, format='{asctime} - {levelname} - {message}', style='{')
logging.basicConfig(level=logging.INFO, format='{asctime} - {levelname} - {message}', style='{')
logger = logging.getLogger(__name__)


USER_TOKEN = os.getenv("USER_TOKEN")
WEBSOCKET_SERVER_URL = os.getenv("WEBSOCKET_SERVER_URL") + USER_TOKEN + "/"




async def handle_incoming_commands(websocket):
    """WebSocket üzerinden gelen komutları işler."""
    while True:
        command_message = await websocket.recv()
        msg = json.loads(command_message)
        msg["event"] = "docker_run"
        docker_info.handle_docker_cmd(msg)

async def send_periodic_docker_info(event_queue):
    """30 saniyede bir Docker container bilgilerini queue'ya ekler."""
    while True:
        try:
            container_info = docker_info.docker_container_info()
            await event_queue.put(json.dumps({"event": "docker_info", "data": container_info}))
            logger.info("Periyodik Docker container bilgileri queue'ya eklendi")
        except Exception as e:
            logger.error(f"Periyodik Docker bilgisi queue'ya eklenirken hata oluştu: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
        await asyncio.sleep(5)



async def send_event_to_websocket(event_queue):
    """Manages WebSocket connection and sends events."""
    max_reconnect_time = 60  # Increased from 10 to 60 seconds
    reconnect_interval = 1  # Increased from 0.5 to 1 second
    backoff_factor = 1.5
    current_interval = reconnect_interval

    while True:
        try:
            async with websockets.connect(WEBSOCKET_SERVER_URL) as websocket:
                logger.info(f"Connected to WebSocket: {WEBSOCKET_SERVER_URL}")
                current_interval = reconnect_interval  # Reset interval on successful connection

                await websocket.send(json.dumps({"event": "docker_info", "data": docker_info.docker_container_info()}))

                incoming_task = asyncio.create_task(handle_incoming_commands(websocket))
                periodic_task = asyncio.create_task(send_periodic_docker_info(event_queue))

                try:
                    while True:
                        event_data = await event_queue.get()
                        await websocket.send(event_data)
                        event_queue.task_done()
                except Exception as e:
                    continue
                finally:
                    incoming_task.cancel()
                    periodic_task.cancel()

        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning(f"WebSocket connection lost: {e}. Attempting to reconnect...")
            sentry_sdk.capture_exception(e)
            
            await asyncio.sleep(current_interval)
            current_interval = min(current_interval * backoff_factor, max_reconnect_time)

        except Exception as e:
            logger.error(f"Unexpected error in WebSocket connection: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            await asyncio.sleep(current_interval)
            current_interval = min(current_interval * backoff_factor, max_reconnect_time)

async def main():
    global docker_info
    docker_info = DockerInfo(event_queue)
    await send_event_to_websocket(event_queue)

if __name__ == "__main__":
    asyncio.run(main())
    ...


# def stream_all_container_logs():
#     client = docker.from_env()

#     # Çalışan tüm containerları alın
#     containers = client.containers.list()

#     for container in containers:
#         print(f"Listening to logs from container: {container.name}")
#         log_stream = container.logs(stream=True, follow=True)

#         # Asenkron olarak logları dinlemek için bir fonksiyon
#         for log in log_stream:
#             print(f"[{container.name}] {log.decode('utf-8').strip()}")

# if __name__ == "__main__":
#     stream_all_container_logs()


# docker run -d --name dockair-standalone -v /var/run/docker.sock:/var/run/docker.sock -v D:/DockerContainer:/app/projects -e USER_TOKEN=your_token_here -e WEBSOCKET_SERVER_URL=ws://dockair-web-1:8000/ws/client/ -e PRODUCTION=false -e PROJECTS_BASE_DIR=/app/projects --network dockair_dockair-network dockair-standalone

# docker run -d --name dockair-standalone -v /var/run/docker.sock:/var/run/docker.sock -v D:/DockerContainer:/app/projects -e USER_TOKEN=caef61c7-3a43-49c3-8cc0-faeacf4b3ee0 -e WEBSOCKET_SERVER_URL=ws://dockair-web-1:8000/ws/client/ -e PRODUCTION=false -e PROJECTS_BASE_DIR=/app/projects --network dockair_dockair-network dockair-standalone
