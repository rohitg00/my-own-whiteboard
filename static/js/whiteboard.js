class Whiteboard {
    constructor(canvasId, socket, roomId) {
        this.canvas = new fabric.Canvas(canvasId, {
            isDrawingMode: true,
            width: window.innerWidth * 0.9,
            height: window.innerHeight * 0.8,
            backgroundColor: '#ffffff',
            selection: false // Disable multiple selection
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
                strokeWidth: this.canvas.freeDrawingBrush.width,
                selectable: false
            });
            this.canvas.add(rect);
        });

        this.canvas.on('mouse:move', (o) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(o.e);
            
            // Calculate dimensions while keeping within bounds
            const width = Math.min(Math.abs(pointer.x - origX), 
                             this.canvas.getWidth() - Math.min(origX, pointer.x));
            const height = Math.min(Math.abs(pointer.y - origY),
                              this.canvas.getHeight() - Math.min(origY, pointer.y));
            
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
            
            // Calculate radius while keeping within bounds
            const maxRadius = Math.min(
                Math.abs(pointer.x - origX),
                Math.abs(pointer.y - origY),
                origX,
                origY,
                this.canvas.getWidth() - origX,
                this.canvas.getHeight() - origY
            );
            
            circle.set({
                radius: maxRadius / 2
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
    constrainToBoundary(point) {
        const width = this.canvas.getWidth();
        const height = this.canvas.getHeight();
        return {
            x: Math.min(Math.max(point.x, 0), width),
            y: Math.min(Math.max(point.y, 0), height)
        };
    }

    setupEventListeners() {
        // Add viewport state tracking
        this.viewportState = {
            zoom: 1,
            pan: { x: 0, y: 0 }
        };
        
        // Add cursor tracking
        this.canvas.on('mouse:move', (opt) => {
            const pointer = this.canvas.getPointer(opt.e);
            this.socket.emit('cursor_move', {
                room: this.roomId,
                userName: this.socket.userName,
                x: pointer.x,
                y: pointer.y
            });
        });

        // Handle other users' cursors
        this.socket.on('cursor_update', (data) => {
            if (data.room === this.roomId && data.userName !== this.socket.userName) {
                this.updateCursor(data);
            }
        });

        this.socket.on('error', (error) => {
            console.error('Socket.IO error:', error);
        });

        // Add undo handler
        this.socket.on('undo_update', (data) => {
            if (data.room === this.roomId) {
                if (this.history.length > 0) {
                    const removed = this.history.pop();
                    this.redoStack.push(removed);
                    this.canvas.remove(removed);
                    this.canvas.renderAll();
                }
            }
        });

        // Add redo handler
        this.socket.on('redo_update', (data) => {
            if (data.room === this.roomId) {
                if (this.redoStack.length > 0) {
                    const restored = this.redoStack.pop();
                    this.history.push(restored);
                    this.canvas.add(restored);
                    this.canvas.renderAll();
                }
            }
        });

        this.canvas.on('mouse:move', (opt) => {
            if (!this.isDrawing) return;
            const pointer = this.canvas.getPointer(opt.e);
            const zoom = this.canvas.getZoom();
            
            // Transform coordinates to account for zoom and pan
            const actualX = (pointer.x - this.canvas.viewportTransform[4]) / zoom;
            const actualY = (pointer.y - this.canvas.viewportTransform[5]) / zoom;
            
            // Constrain to canvas boundaries
            const width = this.canvas.getWidth() / zoom;
            const height = this.canvas.getHeight() / zoom;
            
            const constrained = {
                x: Math.min(Math.max(actualX, 0), width),
                y: Math.min(Math.max(actualY, 0), height)
            };
            
            // Update current path with constrained coordinates
            const path = this.canvas.freeDrawingBrush._points;
            if (path && path.length > 0) {
                path[path.length - 1].x = constrained.x;
                path[path.length - 1].y = constrained.y;
            }
        });

        this.canvas.on('path:created', (e) => {
            console.log('Path created, emitting draw event');
            const path = e.path;
            const points = path.path;
            
            // Constrain all points to canvas boundaries
            if (points) {
                points.forEach(point => {
                    if (point[0] !== 'M' && point[0] !== 'L' && point[0] !== 'Q') return;
                    const constrained = this.constrainToBoundary({
                        x: point[point.length - 2],
                        y: point[point.length - 1]
                    });
                    point[point.length - 2] = constrained.x;
                    point[point.length - 1] = constrained.y;
                });
            }
            
            this.history.push(path);
            this.socket.emit('draw', {
                room: this.roomId,
                path: path.toJSON()
            });
        });

        this.socket.on('draw_update', async (data) => {
            console.log('Received draw update:', data);
            if (data.room === this.roomId) {
                try {
                    const path = data.path;
                    // Apply viewport transformations
                    if (path.objects) {
                        path.objects.forEach(obj => {
                            obj.left = obj.left * this.viewportState.zoom + this.viewportState.pan.x;
                            obj.top = obj.top * this.viewportState.zoom + this.viewportState.pan.y;
                        });
                    }
                    await new Promise(resolve => {
                        fabric.util.enlivenObjects([path], (objects) => {
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

        // Sync viewport state
        this.canvas.on('zoom:changed', () => {
            this.viewportState.zoom = this.canvas.getZoom();
            this.socket.emit('viewport_update', {
                room: this.roomId,
                viewport: this.viewportState
            });
    updateCursor(data) {
        // Remove existing cursor if any
        const existingCursor = this.canvas.getObjects().find(
            obj => obj.type === 'group' && obj.id === `cursor_${data.userName}`
        );
        if (existingCursor) {
            this.canvas.remove(existingCursor);
        }

        // Create new cursor group
        const cursorGroup = new fabric.Group([], {
            left: data.x,
            top: data.y,
            selectable: false,
            id: `cursor_${data.userName}`
        });

        // Add cursor pointer
        const cursor = new fabric.Triangle({
            width: 10,
            height: 10,
            fill: '#ff0000',
            angle: 45
        });

        // Add username text
        const text = new fabric.Text(data.userName, {
            fontSize: 12,
            fill: '#ff0000',
            left: 10,
            top: -15
        });

        cursorGroup.addWithUpdate(cursor);
        cursorGroup.addWithUpdate(text);
        this.canvas.add(cursorGroup);
        this.canvas.renderAll();
    }

        });

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
