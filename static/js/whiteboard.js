class Whiteboard {
    constructor(canvasId, socket, roomId) {
        this.canvas = new fabric.Canvas(canvasId, {
            isDrawingMode: true,
            width: window.innerWidth * 0.9,
            height: window.innerHeight * 0.8,
            backgroundColor: '#ffffff',
            selection: false
        });
        this.socket = socket;
        this.roomId = roomId;
        this.history = [];
        this.redoStack = [];
        this.currentMode = 'draw';
        this.isDrawing = false;
        this.cursors = new Map();
        
        // Initialize viewport state
        this.viewportState = {
            zoom: 1,
            pan: { x: 0, y: 0 }
        };
        
        this.initResponsiveCanvas();
        this.setupTools();
        this.setupEventListeners();
        this.loadExistingDrawings();
        
        // Join room immediately after socket setup
        this.socket.joinRoom(this.roomId);
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

    updateCursor(data) {
        console.log('Updating cursor for:', data.userName);  // Debug log
        
        // Remove existing cursor
        const existingCursor = this.canvas.getObjects().find(
            obj => obj.type === 'group' && obj.id === `cursor_${data.userName}`
        );
        if (existingCursor) {
            this.canvas.remove(existingCursor);
        }

        // Create cursor group
        const cursorGroup = new fabric.Group([], {
            left: data.x,
            top: data.y,
            selectable: false,
            evented: false,
            id: `cursor_${data.userName}`
        });

        // Add cursor pointer
        const cursor = new fabric.Triangle({
            width: 15,
            height: 15,
            fill: '#ff4444',
            angle: 45,
            originX: 'center',
            originY: 'center'
        });

        // Add username text with better visibility
        const text = new fabric.Text(data.userName || 'Anonymous', {
            fontSize: 14,
            fill: '#ff4444',
            backgroundColor: 'rgba(255,255,255,0.8)',
            padding: 5,
            left: 10,
            top: -20,
            originX: 'left',
            originY: 'bottom'
        });

        cursorGroup.addWithUpdate(cursor);
        cursorGroup.addWithUpdate(text);
        this.canvas.add(cursorGroup);
        this.canvas.renderAll();
    }

    setupEventListeners() {
        // Add cursor tracking with throttling
        let lastEmit = 0;
        const EMIT_INTERVAL = 50; // 50ms throttle
        
        this.canvas.on('mouse:move', (opt) => {
            const now = Date.now();
            if (now - lastEmit > EMIT_INTERVAL) {
                const pointer = this.canvas.getPointer(opt.e);
                this.socket.emit('cursor_move', {
                    room: this.roomId,
                    x: pointer.x,
                    y: pointer.y
                });
                lastEmit = now;
            }
        });

        // Handle other users' cursors
        this.socket.on('cursor_update', (data) => {
            console.log('Received cursor update:', data);  // Debug log
            if (data.room === this.roomId && data.userName !== this.socket.userName) {
                this.updateCursor(data);
            }
        });

        // Add viewport state tracking
        this.canvas.on('zoom:changed', () => {
            this.viewportState.zoom = this.canvas.getZoom();
            this.socket.emit('viewport_update', {
                room: this.roomId,
                viewport: this.viewportState
            });
        });

        // Handle viewport updates from other users
        this.socket.on('viewport_update', (data) => {
            if (data.room === this.roomId) {
                this.viewportState = data.viewport;
                this.canvas.setZoom(data.viewport.zoom);
                this.canvas.absolutePan(new fabric.Point(
                    data.viewport.pan.x,
                    data.viewport.pan.y
                ));
            }
        });

        // Handle drawing updates
        this.canvas.on('path:created', (e) => {
            const path = e.path;
            this.history.push(path);
            this.socket.emit('draw', {
                room: this.roomId,
                path: path.toJSON()
            });
        });

        this.socket.on('draw_update', async (data) => {
            if (data.room === this.roomId) {
                try {
                    const path = data.path;
                    await new Promise(resolve => {
                        fabric.util.enlivenObjects([path], (objects) => {
                            objects.forEach(obj => {
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

        // Handle undo/redo
        this.socket.on('undo_update', (data) => {
            if (data.room === this.roomId && this.history.length > 0) {
                const removed = this.history.pop();
                this.redoStack.push(removed);
                this.canvas.remove(removed);
                this.canvas.renderAll();
            }
        });

        this.socket.on('redo_update', (data) => {
            if (data.room === this.roomId && this.redoStack.length > 0) {
                const restored = this.redoStack.pop();
                this.history.push(restored);
                this.canvas.add(restored);
                this.canvas.renderAll();
            }
        });

        // Handle clear board
        this.socket.on('clear_board', () => {
            this.canvas.clear();
        });
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
                strokeWidth: this.canvas.freeDrawingBrush.width,
                selectable: false
            });
            this.canvas.add(rect);
        });

        this.canvas.on('mouse:move', (o) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(o.e);
            
            const width = Math.abs(pointer.x - origX);
            const height = Math.abs(pointer.y - origY);
            
            rect.set({
                width: width,
                height: height,
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
                strokeWidth: this.canvas.freeDrawingBrush.width,
                selectable: false
            });
            this.canvas.add(circle);
        });

        this.canvas.on('mouse:move', (o) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(o.e);
            const radius = Math.sqrt(
                Math.pow(pointer.x - origX, 2) +
                Math.pow(pointer.y - origY, 2)
            ) / 2;
            
            circle.set({
                radius: radius
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

    async loadExistingDrawings() {
        try {
            const response = await fetch(`/room/${this.roomId}/drawings`);
            if (!response.ok) throw new Error('Failed to fetch drawings');
            
            const data = await response.json();
            if (data.drawings && Array.isArray(data.drawings)) {
                for (const path of data.drawings) {
                    if (path && typeof path === 'object') {
                        await new Promise(resolve => {
                            fabric.util.enlivenObjects([path], (objects) => {
                                objects.forEach(obj => {
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

    initResponsiveCanvas() {
        this.resizeCanvas();
        window.addEventListener('resize', () => {
            this.resizeCanvas();
        });
    }

    resizeCanvas() {
        const container = document.querySelector('.whiteboard-container');
        const containerWidth = container.clientWidth;
        const containerHeight = window.innerHeight * 0.8;
        
        let newWidth = containerWidth - 40;
        let newHeight = containerHeight;
        
        this.canvas.setWidth(newWidth);
        this.canvas.setHeight(newHeight);
        this.canvas.renderAll();
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
            this.socket.emit('undo', { 
                room: this.roomId,
                objectData: removed.toJSON()
            });
        }
    }

    redo() {
        if (this.redoStack.length > 0) {
            const restored = this.redoStack.pop();
            this.history.push(restored);
            this.canvas.add(restored);
            this.canvas.renderAll();
            this.socket.emit('redo', { 
                room: this.roomId,
                objectData: restored.toJSON()
            });
        }
    }
}
