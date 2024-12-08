class Whiteboard {
    constructor(canvasId, socket, roomId) {
        this.canvas = new fabric.Canvas(canvasId, {
            isDrawingMode: true
        });
        this.socket = socket;
        this.roomId = roomId;
        this.initResponsiveCanvas();
        this.init();
    }

    init() {
        this.setupTools();
        this.setupEventListeners();
        this.socket.emit('join', { room: this.roomId });
        this.loadExistingDrawings();
    }

    initResponsiveCanvas() {
        // Set initial size
        this.resizeCanvas();
        
        // Add window resize listener
        window.addEventListener('resize', () => {
            this.resizeCanvas();
        });
    }

    resizeCanvas() {
        const container = document.querySelector('.whiteboard-container');
        const containerWidth = container.clientWidth;
        const containerHeight = window.innerHeight * 0.8;
        
        // Store original dimensions if not set
        if (!this.originalWidth) {
            this.originalWidth = this.canvas.width;
            this.originalHeight = this.canvas.height;
        }
        
        // Calculate new dimensions maintaining aspect ratio
        const aspectRatio = this.originalWidth / this.originalHeight;
        let newWidth = containerWidth - 40;
        let newHeight = containerHeight;
        
        if (newWidth / newHeight > aspectRatio) {
            newWidth = newHeight * aspectRatio;
        } else {
            newHeight = newWidth / aspectRatio;
        }
        
        // Set new dimensions
        this.canvas.setWidth(newWidth);
        this.canvas.setHeight(newHeight);
        
        // Calculate and apply scale factor
        const scaleX = newWidth / this.originalWidth;
        const scaleY = newHeight / this.originalHeight;
        
        this.canvas.setZoom(Math.min(scaleX, scaleY));
        this.canvas.renderAll();
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
