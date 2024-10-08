import docker
import json
import asyncio
from threading import Thread
import logging
import sentry_sdk
from datetime import datetime, timedelta
logger = logging.getLogger(__name__)


class DockerInfo:
    def __init__(self, event_queue=None):
        self.client = docker.from_env()
        self.loop = asyncio.get_event_loop()
        self.event_queue = event_queue
        self.container_threads = {}
        self.container_last_log_times = {}

        
        self.initialize_container_threads()

        listener_thread = Thread(
            target=self.docker_event_listener,
            args=(self.loop, self.event_queue),
            daemon=True,
        )
        listener_thread.start()

    def initialize_container_threads(self):
        """Başlangıçta çalışan tüm container'lar için log thread'leri başlatır."""
        containers = self.client.containers.list()
        for container in containers:
            self.start_log_thread(container)

    def start_log_thread(self, container):
        """Bir container için log dinleyen thread başlatır."""
        if container.id not in self.container_threads:
            self.container_last_log_times[container.id] = datetime.now()

            print(f"Listening to logs from container: {container.name}")
            thread = Thread(target=self.process_docker_logs_threaded, args=(container,))
            thread.daemon = True
            thread.start()
            self.container_threads[container.id] = thread  # Thread'i kaydet

    def stop_log_thread(self, container_id):
        """Bir container kapanınca, ilgili log thread'ini sonlandırır."""
        if container_id in self.container_threads:
            print(f"Stopping log thread for container: {container_id}")
            # Thread'i sonlandırmak için, durumu kaydetmemiz ve thread'e sonlandırma sinyali göndermemiz gerekir.
            # Bu örnekte thread'lerin doğal kapanmasına izin veriyoruz.
            del self.container_threads[container_id]  # Thread'i sözlükten sil

    def process_docker_logs_threaded(self, container):
        """Her bir container logunu ayrı bir thread'de dinleyen fonksiyon."""
        try:
            last_log_time = self.container_last_log_times.get(container.id, datetime.now() - timedelta(seconds=1))

            log_stream = container.logs(stream=True, follow=True, since=last_log_time)
            for log in log_stream:
                if container.id not in self.container_threads:
                    break  # Eğer container durduysa, thread'i sonlandır
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_with_timestamp = f"[{timestamp}] {log.decode('utf-8').strip()}"
                print(f"[{container.name}] {log_with_timestamp}")
                self.container_last_log_times[container.id] = datetime.now()

                coro = self.event_queue.put(json.dumps({"event": "log", "log": log_with_timestamp, "id": container.id}))
                future = asyncio.run_coroutine_threadsafe(coro, self.loop)
                future.result()
        except Exception as e:
            logger.error(f"Container {container.name} loglarını işlerken hata oluştu: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)





    def docker_filter(self, event):
        r = {}
        print(self.container_threads)
        if event["Type"] == "container":
            if event["Action"] in [
                "stop",
                "die",
                "kill",
                "start",
            ]:
                r["status"] = event["status"]
                r["id"] = event["id"]
                r["time"] = event["time"]
                r["type"] = event["Type"]


            if event["Action"] in [
                "create",
                "destroy",
            ]:
                
                info = self.docker_container_info()
                r["data"] = info


            if event["Action"] in ["start", "create"]:
                self.start_log_thread(self.client.containers.get(event["id"]))
            elif event["Action"] in ["destroy", "die"]:
                self.stop_log_thread(event["id"])

        return r

    def docker_container_info(self):
        containers = self.client.containers.list(all=True)
        compose_projects = {}
        for container in containers:
            # açık olan containerlerin loglarını thread da başlat

            compose_project = container.labels.get(
                "com.docker.compose.project", container.name
            )
            if compose_project:
                if compose_project not in compose_projects:
                    compose_projects[compose_project] = []
                compose_projects[compose_project].append(
                    {
                        "id": container.id,
                        "name": container.name,
                        "status": container.status,
                        "last_started": container.attrs["State"]["StartedAt"],
                        "ports": container.attrs["HostConfig"]["PortBindings"] if container.attrs["HostConfig"]["PortBindings"] else container.attrs["NetworkSettings"]["Ports"],
                        "image": container.attrs["Config"]["Image"],
                    }
                )

        return compose_projects

    def docker_event_listener(self, loop, event_queue):
        try:
            asyncio.set_event_loop(loop)
            for event in self.client.events(decode=True):
                event_data = self.docker_filter(event)
                if event_data:
                    coro = event_queue.put(json.dumps(event_data))
                    future = asyncio.run_coroutine_threadsafe(coro, loop)
                    future.result()
        except Exception as e:
            logger.error(f"Docker olaylarını dinlerken hata oluştu: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)



    def handle_docker_cmd(self, cmd):
        try:
            command = cmd["command"]
            container_id = cmd["container_id"]
        except Exception as e:
            logger.error(f"Komut işlenirken hata oluştu: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)

        if command == "start_container":
            asyncio.create_task(self._start_container(container_id))
        elif command == "stop_container":
            asyncio.create_task(self._stop_container(container_id))
        elif command == "restart_container":
            asyncio.create_task(self._restart_container(container_id))

    

    async def _start_container(self, container_id):
        await asyncio.to_thread(self.client.containers.get(container_id).start)

    async def _stop_container(self, container_id):
        await asyncio.to_thread(self.client.containers.get(container_id).stop)

    async def _restart_container(self, container_id):
        await asyncio.to_thread(self.client.containers.get(container_id).restart)

