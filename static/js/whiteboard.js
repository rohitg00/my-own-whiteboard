class Whiteboard {
    constructor(canvasId, socket, roomId) {
        this.canvas = new fabric.Canvas(canvasId, {
            isDrawingMode: true,
            width: window.innerWidth * 0.9,
            height: window.innerHeight * 0.8,
        });
        this.socket = socket;
        this.roomId = roomId;
        this.init();
    }

    init() {
        this.setupTools();
        this.setupEventListeners();
        this.socket.emit('join', { room: this.roomId });
        this.loadExistingDrawings();
    }

    async loadExistingDrawings() {
        try {
            const response = await fetch(`/room/${this.roomId}/drawings`);
            if (!response.ok) throw new Error('Failed to fetch drawings');
            
            const data = await response.json();
            if (data.drawings && data.drawings.length > 0) {
                data.drawings.forEach(path => {
                    if (path) {  // Add null check
                        fabric.util.enlivenObjects([path], (objects) => {
                            objects.forEach(obj => {
                                this.canvas.add(obj);
                            });
                            this.canvas.renderAll();
                        });
                    }
                });
            }
        } catch (error) {
            console.error('Error loading existing drawings:', error);
        }
    }

    setupTools() {
        this.canvas.freeDrawingBrush.width = 2;
        this.canvas.freeDrawingBrush.color = '#000000';
    }

    setupEventListeners() {
        this.socket.on('error', (error) => {
            console.error('Socket.IO error:', error);
        });

        this.canvas.on('path:created', (e) => {
            try {
                const path = e.path.toJSON();
                this.socket.emit('draw', {
                    room: this.roomId,
                    path: path
                });
            } catch (error) {
                console.error('Error sending drawing:', error);
            }
        });

        this.socket.on('draw_update', (data) => {
            fabric.util.enlivenObjects([data.path], (objects) => {
                objects.forEach(obj => {
                    this.canvas.add(obj);
                    this.canvas.renderAll();
                });
            });
        });

        this.socket.on('clear_board', () => {
            this.canvas.clear();
        });
    }

    setColor(color) {
        this.canvas.freeDrawingBrush.color = color;
    }

    setBrushSize(size) {
        this.canvas.freeDrawingBrush.width = size;
    }

    clear() {
        this.canvas.clear();
        this.socket.emit('clear', { room: this.roomId });
    }
}
