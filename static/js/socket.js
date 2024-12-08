class SocketManager {
    constructor() {
        this.socket = io();
        this.lastRoom = null;
        this.userName = '';
        this.cursors = new Map();
        this.setupEventListeners();
    }

    setupEventListeners() {
        this.socket.on('connect', () => {
            console.log('Connected to server');
            // Re-join room if reconnecting
            if (this.lastRoom) {
                this.socket.emit('join', { room: this.lastRoom });
            }
        });

        this.socket.on('disconnect', () => {
            console.log('Disconnected from server');
        });

        this.socket.on('connect_error', (error) => {
            console.error('Connection error:', error);
        });

        this.socket.on('user_joined', (data) => {
            const userCount = document.getElementById('activeUsers');
            if (userCount) {
                userCount.textContent = `Users: ${data.count}`;
            }
        });

        this.socket.on('user_left', (data) => {
            const userCount = document.getElementById('activeUsers');
            if (userCount) {
                userCount.textContent = `Users: ${data.count}`;
            }
        });
    }

    setUserName(name) {
        this.userName = name;
    }

    joinRoom(roomId) {
        console.log('Joining room:', roomId);
        this.lastRoom = roomId;
        this.socket.emit('join', { 
            room: roomId,
            userName: this.userName
        });
    }

    emit(event, data) {
        try {
            // Always include userName in events
            const dataWithUser = {
                ...data,
                userName: this.userName || 'Anonymous'  // Ensure userName is never undefined
            };
            console.log('Emitting event with data:', dataWithUser);  // Debug log
            this.socket.emit(event, dataWithUser);
        } catch (error) {
            console.error('Error emitting event:', error);
        }
    }

    on(event, callback) {
        this.socket.on(event, (data) => {
            try {
                callback(data);
            } catch (error) {
                console.error(`Error handling ${event} event:`, error);
            }
        });
    }

    disconnect() {
        if (this.socket) {
            this.socket.disconnect();
        }
    }
}
