# DockAir Standalone Application

This is the standalone application for DockAir, designed to run on client machines and communicate with the main DockAir server.

## Running the Application

docker run -d --name dockair-standalone --restart=always -v /var/run/docker.sock:/var/run/docker.sock -e USER_TOKEN=your_token_here furkankok/dockair-standalone:0.0.1