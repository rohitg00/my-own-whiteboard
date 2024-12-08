class Whiteboard {
    constructor(canvasId, socketManager, roomId) {
        this.canvas = new fabric.Canvas(canvasId);
        this.socket = socketManager;
        this.roomId = roomId;
        this.currentTool = 'pen';
        this.setupCanvas();
        this.setupEventListeners();
        this.setupKeyboardShortcuts();
        
        // Initialize color palette for users
        this.colorPalette = [
            '#E63946', // Red
            '#2A9D8F', // Teal
            '#E9C46A', // Yellow
            '#264653', // Navy
            '#F4A261', // Orange
            '#4A90E2', // Blue
            '#50C878', // Emerald
            '#9B59B6'  // Purple
        ];
        this.userColors = new Map();
        this.history = [];
        this.redoStack = [];

    }

    setupCanvas() {
        this.canvas.setWidth(window.innerWidth);
        this.canvas.setHeight(window.innerHeight - 60);
        this.canvas.isDrawingMode = true;
        this.canvas.freeDrawingBrush.width = 2;
        this.canvas.freeDrawingBrush.color = '#000000';
    }

    setupEventListeners() {
        this.canvas.on('mouse:move', (event) => {
            const pointer = this.canvas.getPointer(event.e);
            this.socket.emit('cursor_move', {
                room: this.roomId,
                userName: this.socket.userName,
                x: pointer.x,
                y: pointer.y
            });
        });

        this.socket.on('cursor_update', (data) => {
            if (data.room === this.roomId) {
                this.updateCursor(data);
            }
        });

        window.addEventListener('resize', () => {
            this.canvas.setWidth(window.innerWidth);
            this.canvas.setHeight(window.innerHeight - 60);
            this.canvas.renderAll();
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

        // Handle undo/redo (simplified)
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

    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
            const cmdKey = isMac ? e.metaKey : e.ctrlKey;

            if (cmdKey) {
                switch(e.key.toLowerCase()) {
                    case 'p':
                        this.setTool('pen');
                        e.preventDefault();
                        break;
                    case 'l':
                        this.setTool('line');
                        e.preventDefault();
                        break;
                    case 'r':
                        this.setTool('rectangle');
                        e.preventDefault();
                        break;
                    case 'c':
                        this.setTool('circle');
                        e.preventDefault();
                        break;
                    case 't':
                        this.setTool('text');
                        e.preventDefault();
                        break;
                    case 'e':
                        this.setTool('eraser');
                        e.preventDefault();
                        break;
                    case 'z':
                        if (e.shiftKey) {
                            this.redo();
                        } else {
                            this.undo();
                        }
                        e.preventDefault();
                        break;
                    case 'y':
                        this.redo();
                        e.preventDefault();
                        break;
                }
            }
        });
    }

    getUserColor(userName) {
        if (!this.userColors.has(userName)) {
            const colorIndex = this.userColors.size % this.colorPalette.length;
            this.userColors.set(userName, this.colorPalette[colorIndex]);
        }
        return this.userColors.get(userName);
    }

    updateCursor(data) {
        console.log('Updating cursor for:', data.userName);
        
        const existingCursor = this.canvas.getObjects().find(
            obj => obj.type === 'group' && obj.id === `cursor_${data.userName}`
        );
        if (existingCursor) {
            this.canvas.remove(existingCursor);
        }

        const userColor = this.getUserColor(data.userName);
        
        const cursor = new fabric.Triangle({
            width: 20,
            height: 20,
            fill: userColor,
            stroke: '#000000',
            strokeWidth: 1,
            angle: 45,
            originX: 'center',
            originY: 'center',
            selectable: false,
            evented: false
        });

        const text = new fabric.Text(data.userName || 'Anonymous', {
            fontSize: 16,
            fill: '#FFFFFF',
            fontFamily: 'Arial',
            fontWeight: 'bold',
            originX: 'center',
            originY: 'center',
            selectable: false,
            evented: false
        });

        const textBg = new fabric.Rect({
            fill: userColor,
            opacity: 0.8,
            width: text.width + 20,
            height: text.height + 10,
            rx: 5,
            ry: 5,
            originX: 'center',
            originY: 'center'
        });

        const textGroup = new fabric.Group([textBg, text], {
            left: 0,
            top: -35,
            selectable: false,
            evented: false
        });

        const cursorGroup = new fabric.Group([cursor, textGroup], {
            left: data.x,
            top: data.y,
            selectable: false,
            evented: false,
            id: `cursor_${data.userName}`
        });

        this.canvas.add(cursorGroup);
        cursorGroup.bringToFront();
        this.canvas.renderAll();
    }

    setTool(toolName) {
        this.currentTool = toolName;
        switch(toolName) {
            case 'pen':
                this.canvas.isDrawingMode = true;
                this.canvas.freeDrawingBrush.width = 2;
                this.canvas.freeDrawingBrush.color = '#000000'; // Reset color for pen
                break;
            case 'eraser':
                this.canvas.isDrawingMode = true;
                this.canvas.freeDrawingBrush.width = 20;
                this.canvas.freeDrawingBrush.color = '#FFFFFF';
                break;
            default:
                this.canvas.isDrawingMode = false;
                break;
        }
    }

    setColor(color) {
        this.canvas.freeDrawingBrush.color = color;
    }

    setBrushSize(size) {
        this.canvas.freeDrawingBrush.width = size;
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

    clear() {
        this.canvas.clear();
        this.socket.emit('clear', { room: this.roomId });
    }
}