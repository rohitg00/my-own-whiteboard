class Whiteboard {
    constructor(canvasId, socket, roomId) {
        this.canvas = new fabric.Canvas(canvasId, {
            isDrawingMode: true,
            width: window.innerWidth * 0.9,
            height: window.innerHeight * 0.8,
            backgroundColor: '#ffffff'
        });
        this.socket = socket;
        this.roomId = roomId;
        this.history = [];
        this.redoStack = [];
        this.currentMode = 'draw';
        this.isDrawing = false;
        this.initResponsiveCanvas();
        this.setupTools();
        
        // Join room immediately after socket setup
        this.socket.joinRoom(this.roomId);
        
        this.setupEventListeners();
        this.loadExistingDrawings();
    }

    setupTools() {
        // Initialize brush settings
        this.canvas.freeDrawingBrush.width = 2;
        this.canvas.freeDrawingBrush.color = '#000000';
        
        // Initialize drawing modes
        this.modes = {
            draw: this.initDrawMode.bind(this),
            rect: this.initRectMode.bind(this),
            circle: this.initCircleMode.bind(this)
        };
        this.setMode('draw');
    }

    setMode(mode) {
        if (!this.modes[mode]) return;
        this.currentMode = mode;
        this.modes[mode]();
        // Update UI to show active mode
        document.querySelectorAll('.tool-group button').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelector(`button[onclick="whiteboard.setMode('${mode}')"]`).classList.add('active');
    }

    initDrawMode() {
        this.canvas.isDrawingMode = true;
        this.canvas.off('mouse:down');
        this.canvas.off('mouse:move');
        this.canvas.off('mouse:up');
    }

    initRectMode() {
        this.canvas.isDrawingMode = false;
        let rect, origX, origY;
        
        this.canvas.on('mouse:down', (o) => {
            this.isDrawing = true;
            const pointer = this.canvas.getPointer(o.e);
            origX = pointer.x;
            origY = pointer.y;
            rect = new fabric.Rect({
                left: origX,
                top: origY,
                width: 0,
                height: 0,
                fill: 'transparent',
                stroke: this.canvas.freeDrawingBrush.color,
                strokeWidth: this.canvas.freeDrawingBrush.width
            });
            this.canvas.add(rect);
        });

        this.canvas.on('mouse:move', (o) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(o.e);
            rect.set({
                width: Math.abs(pointer.x - origX),
                height: Math.abs(pointer.y - origY),
                left: Math.min(origX, pointer.x),
                top: Math.min(origY, pointer.y)
            });
            this.canvas.renderAll();
        });

        this.canvas.on('mouse:up', () => {
            this.isDrawing = false;
            this.history.push(rect);
            this.redoStack = [];
            this.socket.emit('draw', {
                room: this.roomId,
                path: rect.toJSON()
            });
        });
    }

    initCircleMode() {
        this.canvas.isDrawingMode = false;
        let circle, origX, origY;
        
        this.canvas.on('mouse:down', (o) => {
            this.isDrawing = true;
            const pointer = this.canvas.getPointer(o.e);
            origX = pointer.x;
            origY = pointer.y;
            circle = new fabric.Circle({
                left: origX,
                top: origY,
                radius: 0,
                fill: 'transparent',
                stroke: this.canvas.freeDrawingBrush.color,
                strokeWidth: this.canvas.freeDrawingBrush.width
            });
            this.canvas.add(circle);
        });

        this.canvas.on('mouse:move', (o) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(o.e);
            const radius = Math.sqrt(Math.pow(pointer.x - origX, 2) + Math.pow(pointer.y - origY, 2)) / 2;
            circle.set({
                radius: radius,
                left: origX - radius,
                top: origY - radius
            });
            this.canvas.renderAll();
        });

        this.canvas.on('mouse:up', () => {
            this.isDrawing = false;
            this.history.push(circle);
            this.redoStack = [];
            this.socket.emit('draw', {
                room: this.roomId,
                path: circle.toJSON()
            });
        });
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
        
        // Calculate new dimensions maintaining aspect ratio
        let newWidth = containerWidth - 40;
        let newHeight = containerHeight;
        
        // Set new dimensions
        this.canvas.setWidth(newWidth);
        this.canvas.setHeight(newHeight);
        
        this.canvas.renderAll();
    }

    async loadExistingDrawings() {
        try {
            console.log('Loading existing drawings...');
            const response = await fetch(`/room/${this.roomId}/drawings`);
            if (!response.ok) throw new Error('Failed to fetch drawings');
            
            const data = await response.json();
            console.log('Received drawings:', data);
            
            if (data.drawings && Array.isArray(data.drawings)) {
                for (const path of data.drawings) {
                    if (path && typeof path === 'object') {
                        await new Promise(resolve => {
                            fabric.util.enlivenObjects([path], (objects) => {
                                objects.forEach(obj => {
                                    console.log('Adding object to canvas:', obj);
                                    this.canvas.add(obj);
                                    this.history.push(obj);
                                });
                                this.canvas.renderAll();
                                resolve();
                            });
                        });
                    }
                }
            }
        } catch (error) {
            console.error('Error loading existing drawings:', error);
        }
    }

    setupZoom() {
        let lastDistance = 0;
        
        // Add mouse wheel zoom
        this.canvas.on('mouse:wheel', (opt) => {
            const delta = opt.e.deltaY;
            let zoom = this.canvas.getZoom();
            
            // Calculate new zoom
            zoom *= 0.999 ** delta;
            
            // Limit zoom range
            zoom = Math.min(Math.max(0.1, zoom), 20);
            
            // Get mouse position relative to canvas
            const pointer = this.canvas.getPointer(opt.e);
            const point = new fabric.Point(pointer.x, pointer.y);
            
            // Set zoom with point as origin
            this.canvas.zoomToPoint(point, zoom);
            
            opt.e.preventDefault();
            opt.e.stopPropagation();
        });

        // Add touch gesture support
        this.canvas.on('touchstart', (opt) => {
            if (opt.e.touches.length === 2) {
                const touch1 = opt.e.touches[0];
                const touch2 = opt.e.touches[1];
                lastDistance = Math.hypot(
                    touch2.clientX - touch1.clientX,
                    touch2.clientY - touch1.clientY
                );
            }
        });

        this.canvas.on('touchmove', (opt) => {
            if (opt.e.touches.length === 2) {
                const touch1 = opt.e.touches[0];
                const touch2 = opt.e.touches[1];
                const distance = Math.hypot(
                    touch2.clientX - touch1.clientX,
                    touch2.clientY - touch1.clientY
                );
                
                if (lastDistance) {
                    const delta = distance - lastDistance;
                    let zoom = this.canvas.getZoom();
                    zoom *= 1 + (delta / 200);
                    zoom = Math.min(Math.max(0.1, zoom), 20);
                    
                    const center = new fabric.Point(
                        (touch1.clientX + touch2.clientX) / 2,
                        (touch1.clientY + touch2.clientY) / 2
                    );
                    
                    this.canvas.zoomToPoint(center, zoom);
                }
                
                lastDistance = distance;
                opt.e.preventDefault();
            }
        });

        this.canvas.on('touchend', () => {
            lastDistance = 0;
        });
    }

    zoomIn() {
        let zoom = this.canvas.getZoom();
        zoom *= 1.1;
        zoom = Math.min(zoom, 20);
        const center = new fabric.Point(
            this.canvas.width / 2,
            this.canvas.height / 2
        );
        this.canvas.zoomToPoint(center, zoom);
    }

    zoomOut() {
        let zoom = this.canvas.getZoom();
        zoom /= 1.1;
        zoom = Math.max(zoom, 0.1);
        const center = new fabric.Point(
            this.canvas.width / 2,
            this.canvas.height / 2
        );
        this.canvas.zoomToPoint(center, zoom);
    }

    resetZoom() {
        const center = new fabric.Point(
            this.canvas.width / 2,
            this.canvas.height / 2
        );
        this.canvas.zoomToPoint(center, 1);
    }
    setupEventListeners() {
        this.socket.on('error', (error) => {
            console.error('Socket.IO error:', error);
        });

        this.canvas.on('path:created', (e) => {
            console.log('Path created, emitting draw event');
            const path = e.path.toJSON();
            this.socket.emit('draw', {
                room: this.roomId,
                path: path
            });
            this.history.push(e.path);
        });

        this.socket.on('draw_update', async (data) => {
            console.log('Received draw update:', data);
            if (data.room === this.roomId) {
                try {
                    await new Promise(resolve => {
                        fabric.util.enlivenObjects([data.path], (objects) => {
                            objects.forEach(obj => {
                                console.log('Adding received object to canvas');
                                this.canvas.add(obj);
                                this.history.push(obj);
                            });
                            this.canvas.renderAll();
                            resolve();
                        });
                    });
                } catch (error) {
                    console.error('Error handling draw update:', error);
                }
            }
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

    undo() {
        if (this.history.length > 0) {
            const removed = this.history.pop();
            this.redoStack.push(removed);
            this.canvas.remove(removed);
            this.canvas.renderAll();
            this.socket.emit('undo', { room: this.roomId });
        }
    }

    redo() {
        if (this.redoStack.length > 0) {
            const restored = this.redoStack.pop();
            this.history.push(restored);
            this.canvas.add(restored);
            this.canvas.renderAll();
            this.socket.emit('redo', { room: this.roomId });
        }
    }
}
