import docker
import json
import asyncio
from threading import Thread
import logging
import sentry_sdk

logger = logging.getLogger(__name__)


class DockerInfo:
    def __init__(self, event_queue=None):
        self.client = docker.from_env()
        self.loop = asyncio.get_event_loop()
        self.event_queue = event_queue

        listener_thread = Thread(
            target=self.docker_event_listener,
            args=(self.loop, self.event_queue),
            daemon=True,
        )
        listener_thread.start()

    def docker_filter(self, event):
        r = {}
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

        return r

    def docker_container_info(self):
        containers = self.client.containers.list(all=True)
        compose_projects = {}
        for container in containers:
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


# if __name__ == "__main__":
#     docker_info = DockerInfo()
#     for event in docker_info.client.events(decode=True):
#         print(event)


{
    "status": "create",
    "id": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
    "from": "minio/minio:latest",
    "Type": "container",
    "Action": "create",
    "Actor": {
        "ID": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
        "Attributes": {
            "architecture": "x86_64",
            "build-date": "2024-07-18T17:22:52",
            "com.redhat.component": "ubi9-micro-container",
            "com.redhat.license_terms": "https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI",
            "description": "MinIO object storage is fundamentally different. Designed for performance and the S3 API, it is 100% open-source. MinIO is ideal for large, private cloud environments with stringent security requirements and delivers mission-critical availability across a diverse range of workloads.",
            "distribution-scope": "public",
            "image": "minio/minio:latest",
            "io.buildah.version": "1.29.0",
            "io.k8s.description": "Very small image which doesn't install the package manager.",
            "io.k8s.display-name": "Ubi9-micro",
            "io.openshift.expose-services": "",
            "maintainer": "MinIO Inc <dev@min.io>",
            "name": "tender_borg",
            "release": "RELEASE.2024-08-03T04-33-23Z",
            "summary": "MinIO is a High Performance Object Storage, API compatible with Amazon S3 cloud storage service.",
            "url": "https://access.redhat.com/containers/#/registry.access.redhat.com/ubi9/ubi-micro/images/9.4-13",
            "vcs-ref": "cd5996c9b8b99b546584696465f8f39ec682078c",
            "vcs-type": "git",
            "vendor": "MinIO Inc <dev@min.io>",
            "version": "RELEASE.2024-08-03T04-33-23Z",
        },
    },
    "scope": "local",
    "time": 1727592089,
    "timeNano": 1727592089549631314,
}


{
    "status": "start",
    "id": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
    "from": "minio/minio:latest",
    "Type": "container",
    "Action": "start",
    "Actor": {
        "ID": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
        "Attributes": {
            "architecture": "x86_64",
            "build-date": "2024-07-18T17:22:52",
            "com.redhat.component": "ubi9-micro-container",
            "com.redhat.license_terms": "https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI",
            "description": "MinIO object storage is fundamentally different. Designed for performance and the S3 API, it is 100% open-source. MinIO is ideal for large, private cloud environments with stringent security requirements and delivers mission-critical availability across a diverse range of workloads.",
            "distribution-scope": "public",
            "image": "minio/minio:latest",
            "io.buildah.version": "1.29.0",
            "io.k8s.description": "Very small image which doesn't install the package manager.",
            "io.k8s.display-name": "Ubi9-micro",
            "io.openshift.expose-services": "",
            "maintainer": "MinIO Inc <dev@min.io>",
            "name": "tender_borg",
            "release": "RELEASE.2024-08-03T04-33-23Z",
            "summary": "MinIO is a High Performance Object Storage, API compatible with Amazon S3 cloud storage service.",
            "url": "https://access.redhat.com/containers/#/registry.access.redhat.com/ubi9/ubi-micro/images/9.4-13",
            "vcs-ref": "cd5996c9b8b99b546584696465f8f39ec682078c",
            "vcs-type": "git",
            "vendor": "MinIO Inc <dev@min.io>",
            "version": "RELEASE.2024-08-03T04-33-23Z",
        },
    },
    "scope": "local",
    "time": 1727592089,
    "timeNano": 1727592089839599463,
}


{
    "status": "die",
    "id": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
    "from": "minio/minio:latest",
    "Type": "container",
    "Action": "die",
    "Actor": {
        "ID": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
        "Attributes": {
            "architecture": "x86_64",
            "build-date": "2024-07-18T17:22:52",
            "com.redhat.component": "ubi9-micro-container",
            "com.redhat.license_terms": "https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI",
            "description": "MinIO object storage is fundamentally different. Designed for performance and the S3 API, it is 100% open-source. MinIO is ideal for large, private cloud environments with stringent security requirements and delivers mission-critical availability across a diverse range of workloads.",
            "distribution-scope": "public",
            "execDuration": "0",
            "exitCode": "0",
            "image": "minio/minio:latest",
            "io.buildah.version": "1.29.0",
            "io.k8s.description": "Very small image which doesn't install the package manager.",
            "io.k8s.display-name": "Ubi9-micro",
            "io.openshift.expose-services": "",
            "maintainer": "MinIO Inc <dev@min.io>",
            "name": "tender_borg",
            "release": "RELEASE.2024-08-03T04-33-23Z",
            "summary": "MinIO is a High Performance Object Storage, API compatible with Amazon S3 cloud storage service.",
            "url": "https://access.redhat.com/containers/#/registry.access.redhat.com/ubi9/ubi-micro/images/9.4-13",
            "vcs-ref": "cd5996c9b8b99b546584696465f8f39ec682078c",
            "vcs-type": "git",
            "vendor": "MinIO Inc <dev@min.io>",
            "version": "RELEASE.2024-08-03T04-33-23Z",
        },
    },
    "scope": "local",
    "time": 1727592090,
    "timeNano": 1727592090294607328,
}

{
    "status": "destroy",
    "id": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
    "from": "minio/minio:latest",
    "Type": "container",
    "Action": "destroy",
    "Actor": {
        "ID": "57443bdad690057453726f3e8dff3d3c10ce3aee42ed62641f3d8796b68c7095",
        "Attributes": {
            "architecture": "x86_64",
            "build-date": "2024-07-18T17:22:52",
            "com.redhat.component": "ubi9-micro-container",
            "com.redhat.license_terms": "https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI",
            "description": "MinIO object storage is fundamentally different. Designed for performance and the S3 API, it is 100% open-source. MinIO is ideal for large, private cloud environments with stringent security requirements and delivers mission-critical availability across a diverse range of workloads.",
            "distribution-scope": "public",
            "image": "minio/minio:latest",
            "io.buildah.version": "1.29.0",
            "io.k8s.description": "Very small image which doesn't install the package manager.",
            "io.k8s.display-name": "Ubi9-micro",
            "io.openshift.expose-services": "",
            "maintainer": "MinIO Inc <dev@min.io>",
            "name": "tender_borg",
            "release": "RELEASE.2024-08-03T04-33-23Z",
            "summary": "MinIO is a High Performance Object Storage, API compatible with Amazon S3 cloud storage service.",
            "url": "https://access.redhat.com/containers/#/registry.access.redhat.com/ubi9/ubi-micro/images/9.4-13",
            "vcs-ref": "cd5996c9b8b99b546584696465f8f39ec682078c",
            "vcs-type": "git",
            "vendor": "MinIO Inc <dev@min.io>",
            "version": "RELEASE.2024-08-03T04-33-23Z",
        },
    },
    "scope": "local",
    "time": 1727592144,
    "timeNano": 1727592144832906452,
}


{
    "data": {
        "dazzling_khorana": [
            {
                "Id": "8d2ec3de4e40d5c61faaa906cca8d7dc1e3533166bc16acdf156eb5c55ae7572",
                "Name": "dazzling_khorana",
                "Status": "running",
                "LastStarted": "2024-09-29T06:52:31.91300516Z",
            }
        ],
        "dockair": [
            {
                "Id": "a5b183c96e12cdc3b7cb012a818c5a3c0c0c0e5dc73e11b1deec7addba87d490",
                "Name": "dockair-web-1",
                "Status": "exited",
                "LastStarted": "2024-09-29T03:33:38.726378586Z",
            },
            {
                "Id": "ac06dd9e80062192e9ac35e6e9536938de86f2f3a890b98f9271fbf1f0a3f017",
                "Name": "dockair-redis-1",
                "Status": "running",
                "LastStarted": "2024-09-29T05:53:33.975656013Z",
            },
        ],
        "thygutenberg": [
            {
                "Id": "971e367316193d59dcf13ae78a72c05da37d4f66cf8c6fd3ed2b15ce272ed3cc",
                "Name": "thygutenberg-wordpress-1",
                "Status": "exited",
                "LastStarted": "2024-09-29T00:50:29.280763819Z",
            },
            {
                "Id": "938c6016092b8194d64d25ae9cbb1a67cd493b3c7db8401ff9470e11579244c7",
                "Name": "thygutenberg-db-1",
                "Status": "exited",
                "LastStarted": "2024-09-29T06:47:26.685942053Z",
            },
        ],
    }
}
