class SocketManager {
    constructor() {
        this.socket = io();
        this.setupEventListeners();
    }

    setupEventListeners() {
        this.socket.on('connect', () => {
            console.log('Connected to server');
        });

        this.socket.on('disconnect', () => {
            console.log('Disconnected from server');
        });

        this.socket.on('user_joined', (data) => {
            const userCount = document.getElementById('activeUsers');
            userCount.textContent = `Users: ${data.count}`;
        });

        this.socket.on('user_left', (data) => {
            const userCount = document.getElementById('activeUsers');
            userCount.textContent = `Users: ${data.count}`;
        });
    }

    emit(event, data) {
        this.socket.emit(event, data);
    }

    on(event, callback) {
        this.socket.on(event, callback);
    }
}
