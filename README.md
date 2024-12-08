# Real-time Collaborative Whiteboard Application

A real-time collaborative whiteboard application built with Flask, WebSocket, and Redis. The application supports multi-user collaboration, real-time drawing, and persistent storage.

## Features

- Real-time collaborative drawing
- Multi-user support with room-based collaboration
- WebSocket-based real-time updates
- Redis-backed caching and state management
- Docker support with multi-architecture builds (AMD64 and ARM64)
- Production-ready with Gunicorn and eventlet workers

## Tech Stack

- Backend: Flask, Flask-SocketIO
- Real-time: WebSocket, python-socketio
- Cache: Redis
- Database: SQLAlchemy
- Server: Gunicorn with eventlet workers
- Containerization: Docker with multi-arch support

## Quick Start

### Using Docker

```bash
# Pull the image
docker pull rohitghumare64/whiteboard:latest

# Run the container
docker run -d \
  --name whiteboard \
  -p 5001:5000 \
  --env-file .env \
  rohitghumare64/whiteboard:latest
```

The application will be available at http://localhost:5001

### Local Development

1. Clone the repository:
```bash
git clone https://github.com/rohitg00/my-own-whiteboard.git
cd my-own-whiteboard
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables in `.env`:
```
FLASK_APP=app.py
FLASK_ENV=development
FLASK_DEBUG=1
REDIS_URL=your-redis-url
```

4. Run the application:
```bash
python app.py
```

## Docker Support

The application includes multi-architecture Docker support:

- AMD64 (x86_64) for standard PCs and servers
- ARM64 (AArch64) for Apple Silicon Macs and ARM servers

### Building Docker Image

```bash
# Build multi-arch image
docker buildx build --platform linux/amd64,linux/arm64 -t your-username/whiteboard:latest --push .
```

## Contributing

Feel free to open issues and pull requests for any improvements.

## License

MIT License

## Author

Rohit Ghumare (ghumare64@gmail.com)
