import time
import asyncio
import logging
import os
import sys
import sentry_sdk
import websockets
import json
import docker
import threading
from collections import deque

logging.basicConfig(
    level=logging.INFO, format="{asctime} - {levelname} - {message}", style="{"
)
logger = logging.getLogger(__name__)
logger.disabled = True

USER_TOKEN = os.getenv("USER_TOKEN", "e2dd1a2d-5010-4064-8a1c-bcefdf50dcc2")
WEBSOCKET_SERVER_URL = f"wss://dockair.atlantiswebstudio.com/ws/client/{USER_TOKEN}/"

# sentry_sdk.init(
#     dsn="https://dc6f8a680e4b6c81ab27a58b2ed258aa@o4508028078129152.ingest.de.sentry.io/4509029770526800",
#     send_default_pii=True,
# )


class WebsocketManager:
    def __init__(self, connection_url):
        self.connection_url = connection_url
        self.ws = None
        self.connected = False
        self.reconnect_interval = 2
        self.max_reconnect_interval = 60
        self.message_queue = deque()  # Queue to store messages when connection is lost
        self.docker_manager = DockerManager(self)

    async def connect(self):
        while True:
            try:
                self.ws = await websockets.connect(self.connection_url)
                self.connected = True
                self.reconnect_interval = 2
                logger.info("WebSocket connection established")

                await self.process_queued_messages()
                await self.process_incoming_messages()

            except Exception as unexpected_error:
                self.connected = False
                logger.error(f"Unexpected error: {unexpected_error}")

                sentry_sdk.capture_exception(unexpected_error)
                await self.attempt_reconnection()

    async def process_incoming_messages(self):
        try:
            async for message in self.ws:
                try:
                    logger.info(f"Received message: {message}")

                    data = json.loads(message)
                    if "type" not in data:
                        continue

                    if data["type"] == "docker_run":
                        asyncio.create_task(self.docker_manager.manage_container(data["data"]))

                except Exception as message_processing_error:
                    logger.error(
                        f"Error processing message: {message_processing_error}"
                    )
                    sentry_sdk.capture_exception(message_processing_error)
        except Exception as disconnect_error:
            self.connected = False
            logger.warning(f"Connection lost: {disconnect_error}")
            sentry_sdk.capture_exception(disconnect_error)

    async def attempt_reconnection(self):
        logger.info(f"Attempting to reconnect in {self.reconnect_interval} seconds...")
        await asyncio.sleep(self.reconnect_interval)
        self.reconnect_interval = min(
            self.reconnect_interval * 1.5, self.max_reconnect_interval
        )

    async def process_queued_messages(self):
        """Process and send all queued messages"""
        if not self.message_queue:
            return

        logger.info(f"Processing {len(self.message_queue)} queued messages")

        # Create a copy of the queue to iterate through
        queued_messages = list(self.message_queue)
        self.message_queue.clear()

        for msg_type, msg_data in queued_messages:
            success = await self._send_message_internal(msg_type, msg_data)
            if not success:
                # If sending fails, stop processing and re-add remaining messages back to queue
                self.message_queue.appendleft((msg_type, msg_data))
                for remaining_msg in reversed(
                    queued_messages[queued_messages.index((msg_type, msg_data)) + 1 :]
                ):
                    self.message_queue.appendleft(remaining_msg)
                break

    async def _send_message_internal(self, type, data):
        """Internal method to send a message without adding to queue"""
        if not self.connected or self.ws is None:
            return False

        try:
            logger.info(f"Sending message: {type}")
            await self.ws.send(json.dumps({"type": type, "data": data}))
            return True
        except Exception as send_error:
            logger.error(f"Error sending message: {send_error}")
            self.connected = False
            return False

    async def send_message(self, type, data):
        """Add message to queue and attempt to send it"""
        # Always add message to queue first
        self.message_queue.append((type, data))
        logger.info(f"Added message to queue: {type}")

        # If connected, try to send all queued messages
        if self.connected and self.ws is not None:
            return await self.process_queued_messages()
        else:
            logger.warning(
                "Cannot send message: not connected. Message queued for later delivery."
            )
            return False


class DockerManager:
    def __init__(self, websocket_manager: WebsocketManager):
        self.client = docker.from_env()
        self.websocket_manager = websocket_manager
        self.log_threads = {}  # Track log streaming threads

        asyncio.create_task(self.get_docker_info())
        asyncio.create_task(self.listen_docker_events())
        asyncio.create_task(self.listen_container_logs())

    async def _send_docker_info(self):
        containers = self.client.containers.list(all=True)
        container_data = []
        for container in containers:
            container_data.append(container.attrs)

        await self.websocket_manager.send_message("container_data", container_data)

    async def get_docker_info(self):
        while True:
            await self._send_docker_info()
            # Print information about all active threads
            active_threads = threading.enumerate()
            logger.info(f"Active threads count: {len(active_threads)}")
            for thread in active_threads:
                logger.info(f"Thread: {thread.name}, daemon: {thread.daemon}, alive: {thread.is_alive()}")
            await asyncio.sleep(10)

    async def listen_docker_events(self):
        logger.info("Starting Docker event listener")

        while True:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._docker_events_listener, loop)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in Docker event listener setup: {e}")
                sentry_sdk.capture_exception(e)
                await asyncio.sleep(5)

    def _docker_events_listener(self, loop):
        event_filters = {"type": ["container"]}

        try:
            logger.info("Docker events listener thread started")
            for event in self.client.events(decode=True, filters=event_filters):
                coro = self.process_docker_event(event)
                asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as e:
            logger.error(f"Docker events listener thread error: {e}")

    async def process_docker_event(self, event):
        status = event.get("status", "")

        logger.info(f"Docker event: {status}")

        await self.websocket_manager.send_message("docker_event", event)

        if status in ["create", "destroy"]:
            await self._send_docker_info()

        # Update log listeners on container start/stop events
        if status == "start":
            container_id = event.get("id")
            if container_id:
                await self.start_log_stream(container_id)
        elif status == "die" or status == "kill" or status == "stop":
            container_id = event.get("id")
            # Just note that the container stopped, thread will terminate on its own
            logger.info(
                f"Container {container_id} stopped, log streaming will end naturally"
            )

    async def listen_container_logs(self):
        """Start log listeners for all running containers"""
        logger.info("Starting log listeners for all containers")

        while True:
            try:
                # Get currently running containers
                containers = self.client.containers.list()

                # Start log streaming for each running container
                for container in containers:
                    if (
                        container.id not in self.log_threads
                        or not self.log_threads[container.id].is_alive()
                    ):
                        await self.start_log_stream(container.id)

                await asyncio.sleep(30)  # Check for new containers every 30 seconds
            except Exception as e:
                logger.error(f"Error in container logs listener: {e}")
                sentry_sdk.capture_exception(e)
                await asyncio.sleep(10)  # Retry after error

    async def start_log_stream(self, container_id):
        """Start streaming logs for a specific container using a dedicated thread"""
        try:
            # Skip if already streaming
            if (
                container_id in self.log_threads
                and self.log_threads[container_id].is_alive()
            ):
                return

            loop = asyncio.get_running_loop()

            # Create and start a new thread for log streaming
            thread = threading.Thread(
                target=self._stream_container_logs_thread,
                args=(container_id, loop),
                daemon=True,
            )
            self.log_threads[container_id] = thread
            thread.start()

            logger.info(
                f"Started log streaming thread for container: ({container_id})"
            )
        except Exception as e:
            logger.error(
                f"Failed to start log stream for container {container_id}: {e}"
            )
            sentry_sdk.capture_exception(e)

    def _stream_container_logs_thread(self, container_id, loop):
        """Thread function to stream logs from a container and send them directly"""
        try:
            # Get container
            container = self.client.containers.get(container_id)

            # Stream logs
            log_stream = container.logs(
                stream=True,
                follow=True,
                timestamps=True,
                tail=100,
                since=int(time.time() - 10),
            )

            # Process logs and send them immediately
            for log_line in log_stream:
                try:
                    if isinstance(log_line, bytes):
                        log_line = log_line.decode("utf-8", errors="replace").rstrip()

                    log_data = {
                        "container_id": container_id,
                        "message": log_line,
                    }

                    # Send the log directly from the thread to the websocket
                    # using run_coroutine_threadsafe to safely call the async method from a thread
                    asyncio.run_coroutine_threadsafe(
                        self.websocket_manager.send_message("container_log", log_data),
                        loop,
                    )
                except Exception as e:
                    logger.error(f"Error processing log line: {e}")

        except Exception as e:
            logger.error(
                f"Error in log streaming thread for ({container_id}): {e}"
            )
            sentry_sdk.capture_exception(e)


    async def manage_container(self, data):
        container = self.client.containers.get(data["container_id"])
        {
            "start_container": container.start,
            "stop_container": container.stop,
            "restart_container": container.restart,
        }[data["command"]]()

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
        sentry_sdk.capture_exception(system_error)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
