# DockAir Standalone Application

This is the standalone application for DockAir, designed to run on client machines and communicate with the main DockAir server.

## Running the Application

docker run -d --name dockair-standalone --restart=always -v /var/run/docker.sock:/var/run/docker.sock -e WEBSOCKET_SERVER_URL=ws://185.72.9.97:8000/ws/client/ -e USER_TOKEN=your_token_here -e PRODUCTION=false furkankok/dockair-standalone:0.0.1